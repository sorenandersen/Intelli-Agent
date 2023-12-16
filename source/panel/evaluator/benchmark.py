import os
import json
import logging
import time
import boto3
import requests
import json
import itertools

from tenacity import retry, stop_after_attempt
from itertools import product
from typing import Any, Dict, Generator, Iterable, List, Optional, Tuple
from requests_aws4auth import AWS4Auth
from opensearchpy import RequestsHttpConnection

from langchain.docstore.document import Document
from langchain.embeddings import OpenAIEmbeddings
from langchain.embeddings import BedrockEmbeddings
from langchain.vectorstores import FAISS
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.document_loaders import UnstructuredFileLoader
from langchain.document_loaders import UnstructuredMarkdownLoader
from langchain.vectorstores import OpenSearchVectorSearch
from langchain.evaluation import load_evaluator, EvaluatorType
from langchain.llms.bedrock import Bedrock

from llm_bot_dep.loaders.nougat_pdf import NougatPDFLoader
from llm_bot_dep.loaders.markdown import process_md, CustomMarkdownLoader
from llm_bot_dep.splitter_utils import MarkdownHeaderTextSplitter
from llm_bot_dep.sm_utils import create_sagemaker_embeddings_from_js_model, SagemakerEndpointVectorOrCross

from ragas.testset import TestsetGenerator
from langchain.embeddings import OpenAIEmbeddings
from langchain.chat_models import ChatOpenAI
from ragas.llms import LangchainLLM

from dotenv import load_dotenv
load_dotenv(dotenv_path='.env')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# set logger level to debug
logger.setLevel(logging.DEBUG)

AOS_API_SUFFIX = "aos"
LLM_API_SUFFIX = "llm"
embeddingModelEndpoint = os.getenv("EMBEDDING_MODEL_ENDPOINT")
aosEndpoint = os.getenv("AOS_ENDPOINT")
region = os.getenv("REGION")
apiEndpoint = os.getenv("APIEndpointAddress")
openaiApiKey = os.getenv("OPENAI_API_KEY")
openaiApiBase = os.getenv("OPENAI_API_BASE")

credentials = boto3.Session().get_credentials()
awsauth = AWS4Auth(credentials.access_key, credentials.secret_key, region, 'es', session_token=credentials.token)

default_aos_index_name = "llm-bot-index"

metadata_template = {
    "content_type": "paragraph",
    "current_heading": 0,
    "heading_hierarchy": {},    
    "figure_list": [],
    "chunk_id": "$$",
    "file_path": "",
    "keywords": [],
    "summary": "",
}

# prerequisite for testdata generation using ragas, or using OpenAIEmbeddings but need to set the OPENAI_API_KEY/OPENAI_API_BASE in env
bedrock_client = boto3.client("bedrock-runtime", region_name="us-east-1")
bedrock_llm = Bedrock(
    model_id = "anthropic.claude-v2", 
    client = bedrock_client,
    model_kwargs = {'temperature': 0}
)
bedrock_embedding = BedrockEmbeddings(
    # model_id="amazon.titan-embed-text-v1", region_name="us-east-1"
    # model_id="cohere.embed-multilingual-v3", region_name="us-east-1"
    model_id = "amazon.titan-text-express-v1", region_name="us-east-1"
)

def csdc_markdown_loader(file_path: str) -> List[Document]:
    # read content from file_path
    with open(file_path, "r") as f:
        file_content = f.read()
    # placeholder for bucket and key
    bucket = "default"
    key = "default"

    loader = CustomMarkdownLoader(aws_path=f"s3://{bucket}/{key}")
    docs = loader.load(file_content)
    logger.debug("csdc markdown load data: {}".format(docs))
    return docs

def nougat_loader(file_path: str) -> List[Document]:
    loader = NougatPDFLoader(file_path)
    docs = loader.load()
    logger.debug("nougat load data: {}".format(docs))

def llamaIndex_pdf_loader(file_path: str) -> List[Document]:
    try:
        import pypdf
    except ImportError:
        raise ImportError(
            "pypdf is required to read PDF files: `pip install pypdf`"
        )
    with open(file_path, "rb") as fp:
        # Create a PDF object
        pdf = pypdf.PdfReader(fp)

        # Get the number of pages in the PDF document
        num_pages = len(pdf.pages)

        # Iterate over every page
        docs = []
        for page in range(num_pages):
            # Extract the text from the page
            page_text = pdf.pages[page].extract_text()
            page_label = pdf.page_labels[page]

            metadata = {"page_label": page_label, "file_name": file_path}
            logger.debug("page_text: {}, page_label: {}".format(page_text, page_label))
            docs.append(Document(page_content=page_text, metadata=metadata))

def langchain_md_loader(file_path: str) -> List[Document]:
    loader = UnstructuredMarkdownLoader(file_path, mode="elements")
    docs = loader.load()
    logger.debug("langchain md load data: {}".format(docs))
    return docs

def langchain_unstructured_loader(file_path: str) -> List[Document]:
    loader = UnstructuredFileLoader(file_path, mode="elements")
    docs = loader.load()
    logger.debug("unstructured load data: {}".format(docs))
    return docs

def recursive_splitter(docs: List[Document]) -> List[Document]:
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size = 500,
        chunk_overlap  = 30,
        length_function = len,
        add_start_index = True,
    )
    docs = text_splitter.split_documents(docs)
    logger.debug("langchain recursive splitter: {}".format(docs))
    return docs

def csdc_markdown_header_splitter(doc: Document) -> List[Document]:
    markdown_splitter = MarkdownHeaderTextSplitter("default")
    docs = markdown_splitter.split_text(doc)
    logger.debug("csdc markdown header splitter: {}".format(docs))
    return docs

def documents_to_strings(documents: List[Document]) -> List[str]:
    serialized_documents = []
    for doc in documents:
        # Serialize the document into a JSON string
        serialized_doc = json.dumps({
            'page_content': doc.page_content,
            'metadata': doc.metadata,
            'type': doc.type
        })
        serialized_documents.append(serialized_doc)
    return serialized_documents

def openai_embedding(docs: List[Document]) -> List[List[float]]:
    embeddings = OpenAIEmbeddings()
    docs = documents_to_strings(docs)
    embeddings.embed_documents(docs)
    logger.debug("openai embeddings: {}".format(embeddings))
    return embeddings

def csdc_embedding(index: str, doc: Document):
    """
    Embeds the given documents using the CSDC embedding model.

    Args:
        index (str): The name of the index to which the documents will be added.
        document (Document): The document to embed.

    Returns:
        list: A list of embeddings, one for each document.
    """
    page_content = doc.page_content
    metadata = doc.metadata
    payload = json.dumps({
        "aos_index": index,
        "operation": "embed_document",
        "body": {
            "documents": {
                "page_content": page_content,
                "metadata": metadata
            }
        }
    })
    headers = {'Content-Type': 'application/json'}
    logger.debug("payload: {}, apiEndpoint: {}, headers: {}, type: {}".format(payload, apiEndpoint, headers, type(payload)))
    try: 
        response = requests.request("POST", apiEndpoint + 'aos', headers=headers, data=payload)
        logger.debug("response: {}".format(json.loads(response.text)))
        return json.loads(response.text)
    except Exception as e:
        logger.error("error: {}".format(e))
        raise e

def _query_embedding(index: str = default_aos_index_name, query: str = "Hello World") -> List[float] :
    """
    Embeds the given query using the CSDC embedding model.

    Args:
        index (str): The name of the index to which the documents will be added, not used for now.
        query (str): The query to embed.

    Returns:
        list: A list of floats with length of vector dimensions (1024).
    """
    headersList = {
        "Accept": "*/*",
        "Content-Type": "application/json",
    }
    payload = json.dumps({
        "aos_index": index,
        "operation": "embed_query",
        "body": {
            "query": query
        }
    })

    try:
        response = requests.request("POST", apiEndpoint + AOS_API_SUFFIX, data=payload, headers=headersList)
        logger.info("response: {}".format(json.loads(response.text)))
    except Exception as e:
        logger.error("error: {}".format(e))
        raise e
    return response

def aos_retriever(index: str, vector_field: List[float], size: int = 10):
    """

    """
    headersList = {
        "Accept": "*/*",
        "Content-Type": "application/json",
    }
    logger.info("vector_field: {} and type: {}".format(vector_field, type(vector_field)))
    payload = json.dumps({
        "aos_index": index,
        "operation": "query_knn",
        "body": {
            "query": vector_field,
            "size": size,
        }
    })

    try:
        response = requests.request("GET", apiEndpoint + AOS_API_SUFFIX, data=payload, headers=headersList)
        # parse the response and get the query result
        response = json.loads(response.text)
        logger.info("aos retriever response: {}".format(response))
    except Exception as e:
        logger.error("error: {}".format(e))
        raise e
    return response

# utils to run embeddings with metrics of dimension and time
def run_embeddings(embeddings_list, docs: List[str]):
    results = []
    for embed_func in embeddings_list:
        start = time.perf_counter()
        embedding_result = embed_func.embed_documents(docs)
        end = time.perf_counter()
        time_elapsed = end - start
        results.append({
            'Model': embed_func.__class__.__name__,
            'Dimensions': len(embedding_result[0]),
            'time': round(time_elapsed, 4)
        })
    return results

def faiss_retriver(texts: List[str], query: str):
    retriever = FAISS.from_texts(texts, OpenAIEmbeddings()).as_retriever()
    docs = retriever.get_relevant_documents(query)
    logger.debug("retriever docs: {}".format(docs))
    db = FAISS.from_texts(texts, OpenAIEmbeddings())
    docs_with_score = db.similarity_search_with_score(query, 3)
    logger.debug("docs_with_score: {}".format(docs_with_score))
    return docs_with_score

def langchain_evalutor(prediction: str, reference: str, type: str):
    """
    
    """
    evaluator = load_evaluator(type)
    response = evaluator.evaluate_strings(prediction=prediction, reference=reference)
    logger.debug("evaluator response: {}".format(response))
    # {'score': 0.09683692455291748}
    return response

def llama_index_evalutor(query: str, docs_with_score: List[Tuple[str, float]]):
    pass

def testdata_generate(docs: List[Document], llm: str = "bedrock", embedding: str = "bedrock"):
    """
    generate test data for evaluation
    """
    if llm == "bedrock":
        generator_llm = LangchainLLM(llm=bedrock_llm)
        critic_llm = LangchainLLM(llm=bedrock_llm)
    elif llm == "openai":
        generator_llm = LangchainLLM(llm=ChatOpenAI(model="gpt-3.5-turbo", openai_api_key=openaiApiKey, openai_api_base=openaiApiBase))
        critic_llm = LangchainLLM(llm=ChatOpenAI(model="gpt-3.5-turbo", openai_api_key=openaiApiKey, openai_api_base=openaiApiBase))
        # critic_llm = LangchainLLM(llm=ChatOpenAI(model="gpt-4"))
    else:
        raise ValueError(f"Unsupported llm: {llm}")

    # check embedding model
    if embedding == "bedrock":
        embeddings_model = bedrock_embedding
    elif embedding == "openai":
        embeddings_model = OpenAIEmbeddings()

    # Change resulting question type distribution
    testset_distribution = {
        "simple": 0.25,
        "reasoning": 0.5,
        "multi_context": 0.0,
        "conditional": 0.25,
    }

    # percentage of conversational question
    chat_qa = 0.2

    test_generator = TestsetGenerator(
        generator_llm=generator_llm,
        critic_llm=critic_llm,
        embeddings_model=embeddings_model,
        testset_distribution=testset_distribution,
        chat_qa=chat_qa,
    )

    testset = test_generator.generate(loader_res, test_size=10)
    test_df = testset.to_pandas()
    logger.debug("testdata head: {}".format(test_df.head()))

    # Saving to a csv and txt file for debugging purpose
    test_df.to_csv('test_data.csv', index=False)
    test_df.to_csv('test_data.txt', sep='\t', index=False)

class WorkflowExecutor:
    """
    A class to execute a workflow with various components such as loaders, splitters,
    embedders, retrievers, and evaluators.

    Attributes:
        components (dict): A dictionary to store lists of different workflow components.
    """
    def __init__(self):
        """Initializes the WorkflowExecutor with empty lists of components."""
        self.components = {
            'loaders': [],
            'splitters': [],
            'embedders': [],
            'retrievers': [],
            'evaluators': []
        }

    def update_component(self, component_type, component, action):
        """
        Adds or removes a component to/from the respective component list.

        Args:
            component_type (str): The type of component (e.g., 'loaders', 'splitters').
            component (object): The component to add or remove.
            action (str): 'add' to add the component, 'remove' to remove it.

        Raises:
            ValueError: If the component type is invalid.
        """
        if component_type in self.components:
            if action == 'add':
                self.components[component_type].append(component)
            elif action == 'remove' and component in self.components[component_type]:
                self.components[component_type].remove(component)
        else:
            raise ValueError(f"Invalid component type: {component_type}")

    def execute_workflow(self, input_document, query):
        """
        Executes the workflow with all combinations of components and returns the results.

        Args:
            input_document (str): The input document to process.
            query (str): The query for retrieval and evaluation.

        Returns:
            list: A list of results from executing each workflow combination.

        Embedding evaluation: embedding into AOS using solution and langchain with different index , then using same question to query the retrieved references, calculate the similarities score between query and retrieved score, compare the score for both methods.

        E2E LLM evaluation: construct dataset with ground truth and using exiting langchain or llama index library to evaluate the faithfulness, relevance and accuracy metrics, OpenAI or Claude will be used as judger to determine the final score.
        """
        results_matrix = []
        for loader, splitter, embedder, retriever, evaluator in product(
            self.components['loaders'],
            self.components['splitters'],
            self.components['embedders'],
            self.components['retrievers'],
            self.components['evaluators']
        ):
            docs = loader(input_document)
            docs = splitter(docs)
            vectors = embedder.embed_documents(docs)
            retrieved_docs = retriever(docs, query)
            metrics = evaluator(retrieved_docs)
            results_matrix.append(metrics)

        return results_matrix

# Preparing loader, splitter, and embeddings retriever list, iterate them to create comparasion matrix
loader_list = [langchain_unstructured_loader, nougat_loader, csdc_markdown_loader]
splitter_list = [recursive_splitter, csdc_markdown_header_splitter]
embeddings_list = [openai_embedding, csdc_embedding]
retriever_list = [faiss_retriver, aos_retriever]
evalutor_list = [langchain_evalutor]

def batch_generator(generator, batch_size: int):
    iterator = iter(generator)
    while True:
        batch = list(itertools.islice(iterator, batch_size))
        if not batch:
            break
        yield batch

# Debugging purpose
if __name__ == "__main__":
    """
    evaluate the retrieved documents with query and return summary result including metrics below:
    1. # round of experiments
    2. # of evaluation questions
    3. chunk size and overlap size
    4. split method
    5. retrieval method
    6. embedding algorithm & model
    7. # of chunks retrieved
    8. average relevance score of retrival
    9. average similarity score of retrival
    10. average time of retrival
    """
    """
    # prepare for QA dataset
    loader_res = csdc_markdown_loader("md-sample-01.md")
    # testdata_generate(loader_res, llm="openai", embedding="openai")

    # load, muliplex above result
    # loader_res = csdc_markdown_loader("md-sample-02.md")

    # split
    splitter_res = csdc_markdown_header_splitter(loader_res)

    # embedding
    batches = batch_generator(splitter_res, batch_size=5)
    for batch in batches:
        for doc in batch:
            embedding_res = csdc_embedding(default_aos_index_name, doc)
    """
    # retriever
    query = "question 6"
    query_res = _query_embedding('jsonl', query)
    retriver_res = aos_retriever('jsonl', json.loads(query_res.text), 10)
    reference_list = []
    for hit in retriver_res['hits']['hits']:
        reference_list.append(hit['_source']['text'])
    
    # evaluator query with all the reference and save the score into a list
    score_list = []
    for reference in reference_list:
        score_list.append(langchain_evalutor(prediction=query, reference=reference, type=EvaluatorType.EMBEDDING_DISTANCE)['score'])
    logger.info("score_list: {}".format(score_list))

    # overall workflow
    # workflow = WorkflowExecutor()
    # workflow.update_component('loaders', langchain_unstructured_loader, 'add')
    # workflow.update_component('splitters', recursive_splitter, 'add')