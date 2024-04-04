"""Integration Agent"""

# Imports
from typing import List, Optional
import os
import httpx
import base64
import json
import uvicorn
import re
from dotenv import load_dotenv
from fastapi import FastAPI
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.document_loaders import WebBaseLoader
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import GithubFileLoader
from langchain.tools.retriever import create_retriever_tool
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.pydantic_v1 import BaseModel, Field
from langchain_core.messages import BaseMessage
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import asyncio

# Env Variables
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")
tavily_api_key = os.getenv("TAVILY_API_KEY")
access_token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
session_store = {}

# Firestore
cred = credentials.Certificate("firebase_service_account.json")
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

# App
app = FastAPI(
    title="LangChain Server",
    version="1.0",
    description="A simple API server using LangChain's Runnable interfaces",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Loader Tool
def create_loader(docslink: str):
    loader = WebBaseLoader(docslink)
    docs = loader.load()
    text_splitter = RecursiveCharacterTextSplitter()
    documents = text_splitter.split_documents(docs)
    return documents


# Tools
def create_tools(documents):
    embeddings = OpenAIEmbeddings()
    vector = FAISS.from_documents(documents, embeddings)
    retriever = vector.as_retriever()
    retriever_tool = create_retriever_tool(
        retriever,
        "docs_retriever",
        "Search for information about integrating with a travel provider. For any questions about what code to suggest, you must use this tool!",
    )
    search = TavilySearchResults()
    tools = [retriever_tool, search]
    return tools


# Fetch File Content
async def fetch_file_content(url: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        # Assuming the response is JSON and contains a 'content' field encoded in base64
        content_data = json.loads(response.text)
        if (
            "content" in content_data
            and "encoding" in content_data
            and content_data["encoding"] == "base64"
        ):
            # Decode the base64 content
            decoded_content = base64.b64decode(content_data["content"])
            # Convert bytes to string assuming UTF-8 encoding
            return decoded_content.decode("utf-8")
        else:
            # Return an empty string or some error message if 'content' or 'encoding' is not found
            return "Content not found or not in base64 encoding."


# Fetch Capability Data
async def fetch_capability_data(db, doc_path):
    doc_ref = db.document(doc_path)
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict()
    else:
        print(f"No document found for path: {doc_path}")
        return None


# Create Documents
def create_document_for_file(file_name, file_content, llm_response):
    db = firestore.client()
    doc_ref = db.collection("projectFiles").document()
    doc_ref.set(
        {
            "name": file_name,
            "content": file_content,
            "llm_response": llm_response,
            "createdAt": datetime.now(),
        }
    )
    print(f"Document created for file: {file_name} with LLM response")


# Schema
class AgentInvokeRequest(BaseModel):
    input: str = ""
    session_id: str
    docslink: str
    repo: str
    project: str
    suggested_files: Optional[List[str]] = None
    suggested_file_urls: Optional[List[str]] = None
    suggested_file_paths: Optional[List[str]] = None
    capabilityRefs: Optional[List[str]] = None
    chat_history: List[BaseMessage] = Field(
        ...,
        extra={"widget": {"type": "chat", "input": "location"}},
    )


# Agent Route
@app.post("/agent/invoke")
async def agent_invoke(request: AgentInvokeRequest):
    """The Agent"""
    session_id = request.session_id
    project_id = request.project
    db = firestore.client()
    session_data = session_store.get(session_id, {"step": 2})
    documents = create_loader(request.docslink)
    tools = create_tools(documents)
    suggested_files = request.suggested_files
    suggested_file_urls = request.suggested_file_urls
    suggested_file_paths = request.suggested_file_paths
    github_file_contents = await asyncio.gather(
        *[fetch_file_content(url) for url in suggested_file_urls]
    )
    concatenated_github_file_contents = "\n\n---\n\n".join(github_file_contents)
    sanitised_github_file_contents = concatenated_github_file_contents.replace(
        "{", "{{"
    ).replace("}", "}}")
    db = firestore.client()
    capabilities_names = []
    capabilities_endPoints = []
    capabilities_headers = []
    capabilities_routeName = []
    capabilities_errorBody = []
    capabilities_requestBody = []
    capabilities_responseBody = []
    capabilities_responseGuidance = []
    capabilities_requestGuidance = []

    # Fetch capability data
    db = firestore.client()
    if request.capabilityRefs:
        capability_docs = await asyncio.gather(
            *[fetch_capability_data(db, path) for path in request.capabilityRefs]
        )
        for doc_data in capability_docs:
            if doc_data:
                capabilities_names.append(doc_data.get("name", "No name"))
                capabilities_endPoints.append(doc_data.get("endPoint", "No endPoint"))
                capabilities_headers.append(doc_data.get("headers", "No headers"))
                capabilities_routeName.append(doc_data.get("routeName", "No routeName"))
                capabilities_errorBody.append(doc_data.get("errorBody", "No errorBody"))
                capabilities_requestBody.append(
                    doc_data.get("requestBody", "No requestBody")
                )
                capabilities_responseBody.append(
                    doc_data.get("responseBody", "No responseBody")
                )
                capabilities_responseGuidance.append(
                    doc_data.get("responseGuidance", "No responseGuidance")
                )
                capabilities_requestGuidance.append(
                    doc_data.get("requestGuidance", "No requestGuidance")
                )
                sanitized_capabilities_errorBody = [
                    errorBody.replace("{", "{{").replace("}", "}}")
                    for errorBody in capabilities_errorBody
                ]
                sanitized_capabilities_headers = [
                    headers.replace("{", "{{").replace("}", "}}")
                    for headers in capabilities_headers
                ]
                sanitized_capabilities_requestBody = [
                    requestBody.replace("{", "{{").replace("}", "}}")
                    for requestBody in capabilities_requestBody
                ]
                sanitized_capabilities_responseBody = [
                    responseBody.replace("{", "{{").replace("}", "}}")
                    for responseBody in capabilities_responseBody
                ]
                sanitized_capabilities_responseGuidance = [
                    responseGuidance.replace("{", "{{").replace("}", "}}")
                    for responseGuidance in capabilities_responseGuidance
                ]

    if session_data["step"] == 1:
        print("Entering Step 1: Starting doc review...")
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert travel API integration developer, code specialist",
                ),
                (
                    "user",
                    f"1. Review the API provider docs here: {request.docslink}."
                    "2. Return the Payload / request body schema object required for the request, only include the required body parameters and the data structure. Note on each field when it is required."
                    "3. Also return the Response data object and its data structure.",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "docslink": request.docslink,
        }
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False)
        response = await agent_executor.ainvoke(context)
        docReview_response = response.get(
            "output", "No backend endpoint action performed."
        )
        print(f"docReview_response: {docReview_response}")

        document_name = "integrationStrategy.txt"
        strategy_content = docReview_response
        doc_ref = db.collection("projectFiles").document(document_name)
        doc_ref.set(
            {
                "name": document_name,
                "createdAt": datetime.now(),
                "project": db.collection("projects").document(project_id),
                "code": strategy_content,
            }
        )

        print(f"Document {document_name} created/updated with doc review.")

        session_store[session_id] = {
            "step": 2,
            "suggested_files": suggested_files,
            "docReview_response": docReview_response,
        }

        formatted_docReview_response = format_response(docReview_response)
        return {
            "step": 1,
            "message": "Doc review generated",
            "output": formatted_docReview_response,
        }

    elif session_data["step"] == 2:
        print("Entering Step 2: Generating Backend Endpoints...")
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert travel API integration developer, your mission is to generate a backend route in Python.",
                ),
                (
                    "user",
                    "# Start your response with a comment and end your response with a comment.\n"
                    "Create a backend route that acts as an API proxy."
                    "Do not use the provider docs, only use the data provided below for this request:"
                    f"Route name: {capabilities_routeName}."
                    f"Do not hardcode the payload."
                    f"Headers: {sanitized_capabilities_headers}."
                    f"Endpoint url: {capabilities_endPoints}."
                    f"Consider the error logging if required: \n{sanitized_capabilities_errorBody}."
                    "Handle the response."
                    "Ensure you handle allow all CORS."
                    "Use a flask app that will host this backend locally on port 5000."
                    "Add print statements for errors and the response."
                    "Be concise, only respond with the code.",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "capabilities_endPoints": capabilities_endPoints,
            "sanitized_capabilities_headers": sanitized_capabilities_headers,
            "capabilities_routeName": capabilities_routeName,
            "sanitized_capabilities_errorBody": sanitized_capabilities_errorBody,
        }
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
        response = await agent_executor.ainvoke(context)
        backend_endpoint_response = response.get(
            "output", "No backend endpoint action performed."
        )
        formatted_backend_response = format_response(backend_endpoint_response)

        file_created = False
        for file_name in suggested_files:
            if file_name.endswith(".py"):
                doc_ref = db.collection("projectFiles").document(file_name)
                # Use formatted_backend_response here
                doc_ref.set(
                    {
                        "name": file_name,
                        "createdAt": datetime.now(),
                        "project": db.collection("projects").document(project_id),
                        "code": formatted_backend_response,  # Updated to use formatted response
                    }
                )
                print(
                    f"Document created/updated for file: {file_name} with backend endpoint code."
                )
                file_created = True
                break
        if not file_created:
            default_file_name = "app.py"
            doc_ref = db.collection("projectFiles").document(default_file_name)
            # Use formatted_backend_response here as well
            doc_ref.set(
                {
                    "name": default_file_name,
                    "createdAt": datetime.now(),
                    "project": db.collection("projects").document(project_id),
                    "code": formatted_backend_response,  # Updated to use formatted response
                }
            )
            print(
                f"Default document created for file: {default_file_name} with backend endpoint code."
            )

        session_store[session_id] = {
            "step": 3,
            "suggested_files": suggested_files,
            "backend_endpoint_response": backend_endpoint_response,
            # "sanitized_docReview_response": sanitized_docReview_response,
        }

        formatted_backend_response = format_response(backend_endpoint_response)
        return {
            "step": 2,
            "message": "Backend endpoints generated",
            "output": formatted_backend_response,
        }

    elif session_data["step"] == 3:
        print("Entering Step 3: Creating or Updating UI Elements...")
        frontend_function_response = session_data.get("frontend_function_response", "")
        sanitised_frontend_function_response = frontend_function_response.replace(
            "{", "{{"
        ).replace("}", "}}")
        sanitized_backend_endpoint_response = session_data.get(
            "sanitized_backend_endpoint_response", ""
        )
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert travel API integration developer, your mission is to generate required frontend UI elements in React.",
                ),
                (
                    "user",
                    "// Start your response with a comment and end your response with a comment.\n"
                    f"Create for me frontend react UI elements such as form fields (e.g. buttons, text fields, etc) and the display areas for the API responses."
                    "Do not use the provider docs, only use the data provided below for this request:"
                    f"See the required request payload object parameters to know what input fields are needed: {sanitized_capabilities_requestBody}."
                    f"Follow this guidance on how to use the request fields {capabilities_requestGuidance}."
                    f"Structure the response according to the response data object: {sanitized_capabilities_responseBody}."
                    f"Follow this advice to structure the response properly: {sanitized_capabilities_responseGuidance}."
                    "Keep all frontend code in a single component."
                    "No dummy data."
                    "Create the required state fields."
                    "Only return React code. Be concise.",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "suggested_files": suggested_files,
            # "sanitised_frontend_function_response": sanitised_frontend_function_response,
            # "sanitized_backend_endpoint_response": sanitized_backend_endpoint_response,
            # "sanitized_docReview_response": sanitized_docReview_response,
            # "sanitized_capabilities_errorBody": sanitized_capabilities_errorBody,
            "sanitized_capabilities_requestBody": sanitized_capabilities_requestBody,
            "sanitized_capabilities_responseBody": sanitized_capabilities_responseBody,
            "sanitized_capabilities_responseGuidance": sanitized_capabilities_responseGuidance,
        }
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
        response = await agent_executor.ainvoke(context)
        ui_response = response.get("output", "No UI update action performed.")
        formatted_ui_response = format_response(ui_response)

        for index, file_name in enumerate(suggested_files):
            file_path = suggested_file_paths[index]
            doc_ref = db.collection("projectFiles").document(file_name)
            doc_ref.set(
                {
                    "name": file_name,
                    "createdAt": datetime.now(),
                    "project": db.collection("projects").document(project_id),
                    "code": formatted_ui_response,  # Updated to use formatted response
                    "repoPath": file_path,
                }
            )
            print(
                f"Document created/updated for file: {file_name} with path: {file_path} and formatted UI response."
            )

        session_store[session_id] = {
            "step": 4,
            "suggested_files": suggested_files,
            "ui_response": ui_response,
            "sanitized_backend_endpoint_response": sanitized_backend_endpoint_response,
            "sanitised_frontend_function_response": sanitised_frontend_function_response,
            # "sanitized_docReview_response": sanitized_docReview_response,
        }

        return {
            "step": 3,
            "message": "UI components created or updated",
            "output": formatted_ui_response,
        }

    elif session_data["step"] == 4:
        print("Entering Step 4: Calculating required API request handler...")
        backend_endpoint_response = session_data.get("backend_endpoint_response", "")
        ui_response = session_data.get("ui_response", "")
        sanitized_backend_endpoint_response = backend_endpoint_response.replace(
            "{", "{{"
        ).replace("}", "}}")
        sanitized_ui_response = ui_response.replace("{", "{{").replace("}", "}}")

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert travel API integration developer, your mission is to generate a frontend API request handler in React.",
                ),
                (
                    "user",
                    "// Note: Start your response with a comment (using '//') and also end your response with a comment (using '//').\n"
                    f"Generate React code for the frontend API request handler that will handle the request and response to the backend we have defined here: {sanitized_backend_endpoint_response}."
                    "Do not use the provider docs, only use the data provided below for this request:"
                    f"See the UI fields we have here and write the API request handler to handle them: {sanitized_ui_response}."
                    f"See the required request payload object parameters: {sanitized_capabilities_requestBody}."
                    f"Follow this guidance on how to use the request fields {capabilities_requestGuidance}."
                    f"Structure the response fields according to the response data object: {sanitized_capabilities_responseBody}."
                    f"Follow this advice to structure the response properly: {sanitized_capabilities_responseGuidance}"
                    "Return to me the code updated with the frontend API request handler component."
                    "Do not hardcode the request fields, expect to receive it from the input fields."
                    "Keep all frontend code in a single component."
                    f"Route name: {capabilities_routeName}"
                    "Assume the backend will be hosted on on http://localhost:5000/."
                    "Only return React code. Use fetch instead of axios. Be concise.",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "sanitized_backend_endpoint_response": sanitized_backend_endpoint_response,
            "sanitized_capabilities_errorBody": sanitized_capabilities_errorBody,
            "sanitized_capabilities_requestBody": sanitized_capabilities_requestBody,
            "sanitized_capabilities_responseBody": sanitized_capabilities_responseBody,
            "sanitized_capabilities_responseGuidance": sanitized_capabilities_responseGuidance,
            "sanitized_ui_response": sanitized_ui_response,
            "capabilities_routeName": capabilities_routeName,
        }
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
        response = await agent_executor.ainvoke(context)
        frontend_function_response = response.get("output", "No action performed.")

        # Format the frontend_function_response before saving it
        formatted_frontend_function_response = format_response(
            frontend_function_response
        )

        for file_name in suggested_files:
            doc_ref = db.collection("projectFiles").document(file_name)
            # Use formatted_frontend_function_response here
            doc_ref.update({"code": formatted_frontend_function_response})
            print(
                f"Document for file: {file_name} updated with formatted frontend function"
            )

        session_store[session_id] = {
            "step": 9,
            "suggested_files": suggested_files,
            "frontend_function_response": frontend_function_response,  # Original response
            "sanitized_backend_endpoint_response": sanitized_backend_endpoint_response,
        }

        formatted_agent_response = format_response(frontend_function_response)
        return {
            "step": 4,
            "message": "Refactoring performed on suggested files",
            "output": formatted_agent_response,
        }

    elif session_data["step"] == 5:
        print("Entering Step 5: Creating Integration Tests...")
        sanitized_backend_endpoint_response = session_data.get(
            "sanitized_backend_endpoint_response", ""
        )
        sanitised_frontend_function_response = session_data.get(
            "sanitised_frontend_function_response", ""
        )
        sanitized_docReview_response = session_data.get(
            "sanitized_docReview_response", ""
        )
        docslink = request.docslink

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert Travel API Integrator focusing on quality assurance. ",
                ),
                (
                    "user",
                    "Your task now is to create backend in the same language as the provided backend code integration tests for the API provider based on the integration requirements identified in the previous steps."
                    "Consider the functionalities proposed for integration and ensure the tests cover these functionalities effectively."
                    "Write the code for the integration tests, nothing else, literally."
                    f"\n\nIntegration Actions from Step 2:\n{sanitised_frontend_function_response}\n"
                    f"\nBackend Endpoint Result from Step 4:\n{sanitized_backend_endpoint_response}\n"
                    f"\nDocumentation Link: {docslink}\n",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "sanitised_frontend_function_response": sanitised_frontend_function_response,
            "sanitized_backend_endpoint_response": sanitized_backend_endpoint_response,
        }
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
        response = await agent_executor.ainvoke(context)
        integration_tests_result = response.get(
            "output", "No integration tests action performed."
        )
        print(f"Integration Tests result: {integration_tests_result}")

        integration_tests_file_name = "integration_tests.py"
        project_id = request.project
        doc_ref = db.collection("projectFiles").document(integration_tests_file_name)
        doc_ref.set(
            {
                "code": integration_tests_result,
                "createdAt": datetime.now(),
                "name": integration_tests_file_name,
                "project": db.collection("projects").document(project_id),
            }
        )
        print(
            f"Document created for file: {integration_tests_file_name} with integration tests code."
        )

        session_store[session_id] = {
            "step": 8,
            "integration_tests_result": integration_tests_result,
            "sanitized_backend_endpoint_response": sanitized_backend_endpoint_response,
            "sanitized_docReview_response": sanitized_docReview_response,
        }

        formatted_integration_tests_response = format_response(integration_tests_result)
        return {
            "step": 5,
            "message": "Integration tests created",
            "output": formatted_integration_tests_response,
        }

    elif session_data["step"] == 6:
        print("Entering Step 6: Reviewing code for improvements...")
        sanitized_backend_endpoint_response = session_data.get(
            "sanitized_backend_endpoint_response", ""
        )
        sanitized_docReview_response = session_data.get(
            "sanitized_docReview_response", ""
        )

        if suggested_files:
            for file_name in suggested_files:
                if file_name.endswith(".js"):
                    doc_ref = db.collection("projectFiles").document(file_name)
                    doc = doc_ref.get()
                    if doc.exists:
                        frontend_generated_code = doc.to_dict().get("code", "")
                    else:
                        frontend_generated_code = (
                            "No code found in the document for the '.js' file."
                        )
                    break
        sanitised_frontend_generated_code = frontend_generated_code.replace(
            "{", "{{"
        ).replace("}", "}}")
        print(f"sanitised_frontend_generated_code: {sanitised_frontend_generated_code}")

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert travel API integration developer, your mission is to generate a working react file.",
                ),
                (
                    "user",
                    f"Review the code at {sanitised_frontend_generated_code}."
                    "Do not remove any existing code."
                    "No dummy data."
                    f"Add field validation and error logging where possible: {sanitized_capabilities_errorBody}."
                    "Do not change anything besides field validation."
                    "Ensure the code is ready for production use with all required React boilerplate and no hardcoding.",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "sanitised_frontend_generated_code": sanitised_frontend_generated_code,
            # "sanitized_backend_endpoint_response": sanitized_backend_endpoint_response,
            # "docslink": request.docslink,
            "sanitized_capabilities_errorBody": sanitized_capabilities_errorBody,
        }
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False)
        response = await agent_executor.ainvoke(context)
        code_review_response = response.get(
            "output", "No impact analysis action performed."
        )
        print(f"Impact Analysis result: {code_review_response}")

        if (
            suggested_files
            and code_review_response != "No impact analysis action performed."
        ):
            doc_ref.update({"code": code_review_response})
            print(f"Document for file: {file_name} updated with new code.")

        session_store[session_id] = {
            "step": 7,
        }

        formatted_code_review_response = format_response(code_review_response)
        return {
            "step": 6,
            "message": "Code review completed",
            "output": formatted_code_review_response,
        }

    elif session_data["step"] == 7:
        print("Entering Step 7: Branding and styling...")
        print(f"sanitised_github_file_contents: {sanitised_github_file_contents}")

        if suggested_files:
            for file_name in suggested_files:
                if file_name.endswith(".js"):
                    doc_ref = db.collection("projectFiles").document(file_name)
                    doc = doc_ref.get()
                    if doc.exists:
                        frontend_generated_code = doc.to_dict().get("code", "")
                    else:
                        frontend_generated_code = (
                            "No code found in the document for the '.js' file."
                        )
                    break
        sanitised_frontend_generated_code = frontend_generated_code.replace(
            "{", "{{"
        ).replace("}", "}}")

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert travel API integration developer, your mission is to style the frontend code.",
                ),
                (
                    "user",
                    f"Review the new code at {sanitised_frontend_generated_code}."
                    f"Review the existing page {sanitised_github_file_contents}."
                    "Add inline styling to new code to match the styling patterns from the existing page."
                    "Do not remove any code."
                    "No dummy data.",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "sanitised_frontend_generated_code": sanitised_frontend_generated_code,
            "sanitised_github_file_contents": sanitised_github_file_contents,
        }
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False)
        response = await agent_executor.ainvoke(context)
        styling_response = response.get(
            "output", "No impact analysis action performed."
        )
        print(f"styling_response: {styling_response}")

        if (
            suggested_files
            and styling_response != "No impact analysis action performed."
        ):
            doc_ref.update({"code": styling_response})
            print(f"Document for file: {file_name} updated with new code.")

        session_store[session_id] = {
            "step": 8,
        }

        formatted_styling_response = format_response(styling_response)
        return {
            "step": 7,
            "message": "Code review completed",
            "output": formatted_styling_response,
        }

    elif session_data["step"] == 8:
        print("Entering Step 8: Documentation...")
        sanitized_backend_endpoint_response = session_data.get(
            "sanitized_backend_endpoint_response", ""
        )
        sanitised_frontend_generated_code = session_data.get(
            "sanitised_frontend_generated_code", ""
        )
        # sanitized_docReview_response = session_data.get(
        #     "sanitized_docReview_response", ""
        # )

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "You are a travel API integration documentation expoert."),
                (
                    "user",
                    "Write documentation for the following integration."
                    f"1. Backend endpoint: {sanitized_backend_endpoint_response}."
                    f"2. Frontend component: {sanitised_frontend_generated_code}."
                    f"3. API Provider docs: {request.docslink}"
                    "It should contain the following sections: Quick start guide, testing options (not that we have written tests at integration_tests.py), troubleshooting guide, support contact info, links to API provider docs.",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "docslink": request.docslink,
            "sanitised_frontend_generated_code": sanitised_frontend_generated_code,
            "sanitized_backend_endpoint_response": sanitized_backend_endpoint_response,
        }
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False)
        response = await agent_executor.ainvoke(context)
        documentation_result = response.get(
            "output", "No documentation action performed."
        )
        print(f"Documentation result: {documentation_result}")

        if capabilities_endPoints:
            endpoint_for_filename = capabilities_endPoints[0].replace("/", "_")
            documentation_file_name = (
                f"TechnicalDocumentation_{endpoint_for_filename}.txt"
            )
        else:
            documentation_file_name = "TechnicalDocumentation.txt"

        documentation_file_name = documentation_file_name.replace(":", "_").replace(
            "?", "_"
        )

        project_id = request.project
        doc_ref = db.collection("projectFiles").document(documentation_file_name)
        doc_ref.set(
            {
                "code": documentation_result,
                "createdAt": datetime.now(),
                "name": documentation_file_name,
                "project": db.collection("projects").document(project_id),
            }
        )
        print(f"Documentation saved as {documentation_file_name}")

        session_store[session_id] = {
            "step": 9,
        }

        formatted_documentation_result = format_response(documentation_result)
        return {
            "step": 8,
            "message": "Documentation sent",
            "output": formatted_documentation_result,
        }

    elif session_data["step"] == 9:
        print("Entering Step 9: API Key section...")

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert travel API integration developer."
                    f"1. Search the API providers link: {request.docslink} and learn their process for getting and using the API key."
                    f"2. Provide the steps for me to get and add the API key in my project in a list format. I'm only concerned about the actual API key, nothing else.",
                ),
                (
                    "user",
                    "You are an expert travel API integration developer."
                    f"1. Search the API providers link: {request.docslink} and learn their process for getting and using the API key."
                    f"2. Provide the steps for me to get and add the API key in my project in a list format. I'm only concerned about the actual API key, nothing else."
                    "Include full URLs if they are available for the steps.",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "docslink": request.docslink,
        }
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False)
        response = await agent_executor.ainvoke(context)
        backend_apiKey_result = response.get(
            "output", "No backend endpoint action performed."
        )

        session_store[session_id] = {
            "step": 10,
        }

        steps_list = backend_apiKey_result.split("\n")

        return {
            "step": 9,
            "message": "API Key info sent",
            "output": steps_list,
        }


def format_response(frontend_function_response):
    cleaned_response = re.sub(r"```(python|jsx)\n?", "", frontend_function_response)
    cleaned_response = cleaned_response.replace("```", "")
    return cleaned_response


# Root Route
@app.get("/")
async def root():
    return {"message": "Hello World"}


# Server
if __name__ == "__main__":
    uvicorn.run(
        "serve:app", host="localhost", port=8000, log_level="debug", reload=True
    )
