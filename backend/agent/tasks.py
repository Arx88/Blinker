from celery_app import celery_app
from utils.logger import logger # Assuming your logger is accessible here
# We will need to import more things here later, like DBConnection, 
# parts of agent/api.py logic, update_agent_run_status, etc.
import time
import asyncio
import json
from typing import Optional, Dict, Any
import traceback
from datetime import datetime, timezone, timedelta

# Imports from Suna RAG/Agent codebase
from services.supabase import DBConnection # For type hinting if needed, actual client via run_utils
from supabase import AsyncClient # Added for type hinting
from services import redis as redis_service # For direct use if any, actual init via run_utils
from agentpress.thread_manager import ThreadManager
from agent.run import run_agent # This is the core agent execution logic
# Import the utility functions we moved
from agent.run_utils import (
    update_agent_run_status,
    stop_agent_run,
    _cleanup_redis_instance_key,
    _cleanup_redis_response_list, # Though stop_agent_run calls this
    get_db_connection, # UPDATED
    ensure_redis_initialized,
    worker_init_services, # For connecting to Celery signals
    worker_shutdown_services, # For connecting to Celery signals
    REDIS_RESPONSE_LIST_TTL # Import constant
)
from utils.config import config # For default model name
from sandbox.sandbox import Sandbox # Added for type hint, actual object comes from params
from celery.signals import worker_process_init, worker_process_shutdown
from litellm import embedding  # For generating embeddings
import pypdf
import docx
import io
import tiktoken # For token counting / advanced chunking if needed

# Semantic Chunking imports
import nltk
from sentence_transformers import SentenceTransformer, util

# Specific error imports
from litellm import exceptions as litellm_exceptions
from pypdf import errors as pypdf_errors

# Trafilatura for URL processing
import trafilatura

# Add missing import for UUID and hashlib
import uuid
import hashlib

# Connect Celery signals for worker process init and shutdown
@worker_process_init.connect
def init_worker_services(**kwargs):
    logger.info("Worker process init: Initializing services via Celery signal.")
    asyncio.run(worker_init_services())

@worker_process_shutdown.connect
def shutdown_worker_services(**kwargs):
    logger.info("Worker process shutdown: Shutting down services via Celery signal.")
    asyncio.run(worker_shutdown_services())

@celery_app.task(bind=True, acks_late=True, reject_on_worker_lost=True, default_retry_delay=5*60, max_retries=3)
async def execute_agent_processing(self,
                                 agent_run_id: str,
                                 project_id: str, # ADDED
                                 thread_id: str,
                                 model_name: str,
                                 enable_thinking: bool,
                                 reasoning_effort: str,
                                 stream: bool, # This task's stream param - how it affects run_agent call needs care
                                 enable_context_manager: bool,
                                 user_id: str,
                                 instance_id: str,
                                 initial_prompt_message: Optional[Dict[str, Any]] = None
                                 ):
    """
    Celery task to encapsulate the agent processing logic, adapted from agent.api.run_agent_background.
    acks_late=True means the task message will be acknowledged after the task has been executed, not just before.
    reject_on_worker_lost=True will cause the task to be re-queued if the worker process executing it crashes.
    """
    logger.info(f"[Task {self.request.id}] EXECUTE_AGENT_PROCESSING for agent_run_id: {agent_run_id}, project_id: {project_id}, thread_id: {thread_id}")

    db_connection = await get_db_connection() # Use the new function to get DBConnection object
    db_client = await db_connection.client # Get client if needed for direct use, e.g. update_agent_run_status

    active_run_key = f"active_run:{instance_id}:{agent_run_id}"
    control_channel_instance = f"agent_run:{agent_run_id}:control:{instance_id}"
    control_channel_global = f"agent_run:{agent_run_id}:control"
    stop_event = asyncio.Event()

    tm = ThreadManager(db_connection_override=db_connection) # Pass DBConnection object

    # Handle initial_prompt_message by adding it to the thread
    if initial_prompt_message:
        try:
            msg_role = initial_prompt_message.get('role', 'user')
            msg_content = initial_prompt_message.get('content')
            if msg_content: # Ensure there is content
                logger.info(f"[Task {self.request.id}] Adding initial prompt message to thread {thread_id} for agent_run {agent_run_id}")
                await tm.add_message(
                    thread_id=thread_id,
                    type=msg_role,
                    content=msg_content,
                    is_llm_message=(msg_role == 'assistant') # Or determine based on actual role
                )
            else:
                logger.warning(f"[Task {self.request.id}] Initial prompt message for {agent_run_id} lacked content.")
        except Exception as e:
            logger.error(f"[Task {self.request.id}] Failed to add initial prompt message for {agent_run_id}: {e}", exc_info=True)
            # Decide if this is a fatal error for the task
            await update_agent_run_status(agent_run_id, status="failed", error=f"Failed to process initial message: {str(e)}", db_client_override=db_client)
            return {"status": "failed", "agent_run_id": agent_run_id, "error": "Failed to process initial message"}


    async def check_for_stop_signal():
        pubsub = None
        try:
            # Ensure redis is up for this new connection (already handled by worker_init_services)
            # await ensure_redis_initialized() 
            redis_cli = await redis_service.get_async_client() # Get a client instance
            pubsub = redis_cli.pubsub()
            await pubsub.subscribe(control_channel_instance, control_channel_global)
            logger.info(f"[Task {self.request.id}] Subscribed to {control_channel_instance} and {control_channel_global} for stop signals.")
            while not stop_event.is_set():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
                if message and message["type"] == "message":
                    if message["data"].decode('utf-8') == "STOP":
                        logger.info(f"[Task {self.request.id}] STOP signal received for {agent_run_id} on {message['channel'].decode('utf-8')}. Setting stop_event.")
                        stop_event.set()
                        break
                await asyncio.sleep(0.1) 
        except asyncio.CancelledError:
            logger.info(f"[Task {self.request.id}] Stop signal listener cancelled for {agent_run_id}.")
        except Exception as e:
            logger.error(f"[Task {self.request.id}] Error in stop signal listener for {agent_run_id}: {e}", exc_info=True)
        finally:
            if pubsub:
                try:
                    await pubsub.unsubscribe(control_channel_instance, control_channel_global)
                    # Safely close pubsub if it's a real connection object, redis-py's pubsub objects are typically closed with the connection
                    # If redis_cli is the main connection, it's managed by worker_shutdown_services.
                    # If pubsub has its own close method distinct from the connection: await pubsub.close()
                    logger.debug(f"[Task {self.request.id}] Unsubscribed from pubsub for stop signal listener.")
                except Exception as e:
                    logger.error(f"[Task {self.request.id}] Error cleaning up pubsub for stop signal: {e}", exc_info=True)
            logger.info(f"[Task {self.request.id}] Stop signal listener for {agent_run_id} terminated.")

    stop_signal_task = asyncio.create_task(check_for_stop_signal())
    current_status = "running" 

    try:
        logger.info(f"[Task {self.request.id}] Setting active_run key in Redis: {active_run_key}")
        redis_cli = await redis_service.get_async_client()
        await redis_cli.set(active_run_key, json.dumps({"status": "running", "started_at": datetime.now(timezone.utc).isoformat()}), ex=REDIS_RESPONSE_LIST_TTL)

        final_assistant_messages = [] # Renamed to clarify content
        response_list_key = f"agent_run:{agent_run_id}:responses" # For assistant messages primarily

        async for item in run_agent(
            thread_id=thread_id,
            project_id=project_id,
            stream=False, # Force stream=False for run_agent's internal LLM calls to get whole messages per turn
            thread_manager=tm,
            model_name=model_name or config.MODEL_TO_USE,
            enable_thinking=enable_thinking,
            reasoning_effort=reasoning_effort,
            enable_context_manager=enable_context_manager
        ):
            if isinstance(item, dict):
                item_type = item.get('type')

                if item_type == 'assistant':
                    # This is a direct assistant message object yielded by ResponseProcessor
                    message_obj = item
                    if message_obj.get('role') == 'assistant': # Confirming role
                        final_assistant_messages.append(message_obj)
                        try:
                            await redis_cli.rpush(response_list_key, json.dumps(message_obj))
                            await redis_cli.expire(response_list_key, REDIS_RESPONSE_LIST_TTL)
                            logger.debug(f"[Task {self.request.id}] Appended assistant message to Redis list {response_list_key}")
                        except Exception as e_redis:
                            logger.error(f"[Task {self.request.id}] Failed to push assistant message to Redis list {response_list_key}: {e_redis}", exc_info=True)
                    else:
                        logger.warning(f"[Task {self.request.id}] Received item with type 'assistant' but unexpected role: {message_obj.get('role')}")
                
                elif item_type == 'status':
                    content = item.get('content', {})
                    status_type = content.get('status_type')
                    logger.info(f"[Task {self.request.id}] Agent run {agent_run_id} event: {status_type} - Details: {content}")
                    if status_type == 'tool_error':
                        # Logged for diagnostics, error will be in agent_runs.error if it leads to overall failure
                        logger.error(f"[Task {self.request.id}] Tool error during agent run {agent_run_id}: {content.get('error_details')}")
                    elif status_type == 'thread_run_failed':
                        logger.error(f"[Task {self.request.id}] Agent run {agent_run_id} reported as failed by ResponseProcessor: {content.get('error')}")
                        # This might be an early signal to fail the task

                # Tool messages are saved to the DB by ResponseProcessor but not yielded directly as message objects in non-streaming mode.
                # They are recorded in the 'messages' table. The 'agent_runs.responses' field typically stores assistant outputs.
                # Thus, we are not explicitly collecting 'tool' role messages here for final_assistant_messages.

                elif isinstance(item, dict) and item.get('type') == 'content' and "Agent reached maximum auto-continue limit" in str(item.get('content')):
                    logger.warning(f"[Task {self.request.id}] Agent reached max auto-continue limit for run {agent_run_id}.")
                # else:
                    # logger.debug(f"[Task {self.request.id}] Agent for run {agent_run_id} yielded unhandled item type '{item_type}': {item}")
            else:
                logger.warning(f"[Task {self.request.id}] Agent for run {agent_run_id} yielded non-dict item: {item}")

        if stop_event.is_set():
            logger.info(f"[Task {self.request.id}] Agent run {agent_run_id} was stopped by signal.")
            current_status = "stopped"
            await update_agent_run_status(agent_run_id, status=current_status, error="Manually stopped by user/system.", responses=final_assistant_messages, db_client_override=db_client)
        else:
            logger.info(f"[Task {self.request.id}] Agent run {agent_run_id} completed normally.")
            current_status = "completed"
            await update_agent_run_status(agent_run_id, status=current_status, responses=final_assistant_messages, db_client_override=db_client)

        return {"status": current_status, "agent_run_id": agent_run_id, "final_responses_summary": final_assistant_messages[:3] if final_assistant_messages else None}

    except asyncio.CancelledError:
        logger.warning(f"[Task {self.request.id}] Agent run {agent_run_id} was cancelled.")
        current_status = "failed" 
        try:
            await update_agent_run_status(agent_run_id, status=current_status, error="Task cancelled (worker lost or revoked)", db_client_override=db_client)
        except Exception as db_e:
            logger.error(f"[Task {self.request.id}] Failed to update DB status for cancelled task {agent_run_id}: {db_e}")
        raise 
    except Exception as e:
        logger.error(f"[Task {self.request.id}] Exception in agent run {agent_run_id}: {str(e)}\n{traceback.format_exc()}")
        current_status = "failed"
        try:
            await update_agent_run_status(agent_run_id, status=current_status, error=str(e), db_client_override=db_client)
        except Exception as db_e:
            logger.error(f"[Task {self.request.id}] Failed to update DB status for failed task {agent_run_id}: {db_e}")
        raise 
    finally:
        logger.info(f"[Task {self.request.id}] Cleaning up for agent_run {agent_run_id}, final status: {current_status}")
        stop_event.set() 
        if stop_signal_task:
            try:
                await asyncio.wait_for(stop_signal_task, timeout=5.0) 
            except asyncio.TimeoutError:
                logger.warning(f"[Task {self.request.id}] Timeout waiting for stop_signal_task to finish for {agent_run_id}.")
                stop_signal_task.cancel() 
            except Exception as e_stop_task:
                logger.error(f"[Task {self.request.id}] Exception during stop_signal_task cleanup: {e_stop_task}")

        await _cleanup_redis_instance_key(agent_run_id, instance_id)

        if current_status not in ["completed"]:
             logger.info(f"[Task {self.request.id}] Run did not complete successfully ({current_status}), calling stop_agent_run for full cleanup for {agent_run_id}.")
             await stop_agent_run(agent_run_id, 
                                  error_message=f"Run ended with status: {current_status}", 
                                  instance_id_for_cleanup=instance_id)
        else:
            await _cleanup_redis_response_list(agent_run_id)
            
        logger.info(f"[Task {self.request.id}] Finished execute_agent_processing for {agent_run_id}")

# Constants for chunking (can be refined)
# These values are just examples, you might need to adjust them.
# Based on OpenAI's ada-002 context window (8191 tokens) and typical chunk sizes for RAG.
# A chunk size of 512-1024 tokens is often a good starting point.
# We'll use character count for simplicity first, then can move to token-based.
# AVG Token length is ~4 chars. So 512 tokens ~ 2048 chars.
# CHUNK_SIZE_CHARS = 2000  # Commented out as it's no longer used by semantic chunking
# CHUNK_OVERLAP_CHARS = 200 # Commented out as it's no longer used by semantic chunking

# --- New constants for Semantic Chunking ---
# Threshold for cosine similarity: if similarity between sentence N and N+1 is BELOW this, it's a potential break.
SEMANTIC_SIMILARITY_BREAK_THRESHOLD = 0.45 # Adjusted based on typical E5 behavior (lower means more breaks)
# Max tokens for a chunk (using cl100k_base for estimation, similar to ada-002 and other LLMs)
MAX_CHUNK_TOKENS = 500
# Min tokens for a chunk to avoid very small, possibly noisy chunks from too many semantic breaks.
MIN_CHUNK_TOKENS = 50
# --- End New constants ---

# Initialize tokenizer for token counting
try:
    tokenizer = tiktoken.get_encoding("cl100k_base")
except Exception as e:
    logger.error(f"Failed to load tiktoken tokenizer: {e}. Falling back to GPT-2 tokenizer.")
    tokenizer = tiktoken.get_encoding("gpt2") # Fallback

# Initialize Sentence Transformer Model and NLTK sentence tokenizer
# This will be loaded once per worker process when the module is imported.
SENTENCE_MODEL_NAME = "intfloat/multilingual-e5-large"
try:
    logger.info(f"Attempting to download NLTK 'punkt' model if not present...")
    try:
        nltk.data.find('tokenizers/punkt')
    except LookupError: # Changed from nltk.downloader.DownloadError
        logger.info(f"NLTK 'punkt' model not found. Downloading...")
        nltk.download('punkt', quiet=True)
    logger.info(f"NLTK 'punkt' model should be available.")
    
    logger.info(f"Loading sentence transformer model: {SENTENCE_MODEL_NAME}...")
    sentence_model = SentenceTransformer(SENTENCE_MODEL_NAME)
    logger.info(f"Sentence transformer model {SENTENCE_MODEL_NAME} loaded successfully.")
except Exception as e:
    logger.error(f"CRITICAL: Failed to load sentence_transformer model ({SENTENCE_MODEL_NAME}) or NLTK 'punkt': {e}", exc_info=True)
    # If the model can't load, semantic chunking won't work. 
    # We might want a fallback or to prevent tasks from running. For now, log critical error.
    sentence_model = None 

# Helper function to update document status in the database
async def _update_kb_document_status(db: AsyncClient, document_id: str, status: str, error_message: str = None):
    try:
        update_data = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}
        if error_message:
            update_data["error_message"] = error_message
        
        await db.table("knowledge_base_documents").update(update_data).eq("id", document_id).execute()
        logger.info(f"Updated KB document {document_id} status to {status}")
    except Exception as e:
        logger.error(f"Failed to update KB document {document_id} status to {status}: {e}")

# Helper function for basic character-based chunking (Now Deprecated)
# def chunk_text_by_char(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
#     chunks = []
#     start_index = 0
#     text_len = len(text)
#     while start_index < text_len:
#         end_index = min(start_index + chunk_size, text_len)
#         chunks.append(text[start_index:end_index])
#         start_index += chunk_size - chunk_overlap
#         if start_index >= end_index and start_index < text_len: # Ensure progress if overlap is large or chunk is small
#             start_index = end_index 
#     return chunks

# --- New Semantic Chunking Function ---
def chunk_text_semantically(text: str) -> list[str]:
    if not sentence_model:
        logger.error("Sentence model not loaded. Cannot perform semantic chunking. Falling back to basic text splitting (not implemented yet as fallback here).")
        # As a very basic fallback if model loading failed, split by paragraphs or a large fixed token count.
        # For now, returning the whole text as one chunk if model is missing (can be very large).
        # A proper fallback to character-based or simple token split should be implemented if this is a concern.
        # This will likely cause issues downstream if the text is large.
        return [text] if text.strip() else []

    if not text or not text.strip():
        logger.info("Input text is empty or whitespace only. Returning no chunks.")
        return []

    try:
        sentences = nltk.sent_tokenize(text)
    except Exception as e:
        logger.error(f"NLTK sent_tokenize failed: {e}. Returning text as a single chunk.", exc_info=True)
        return [text] # Fallback if sentence tokenization fails

    if not sentences:
        logger.info("No sentences found after tokenization. Returning no chunks.")
        return []
    
    logger.debug(f"Original text split into {len(sentences)} sentences.")

    # Handle very short texts or texts with few sentences
    if len(sentences) <= 1:
        # If only one sentence (or zero after filter), check its token count
        single_chunk_text = " ".join(sentences).strip()
        if not single_chunk_text: return []
        
        tokens = tokenizer.encode(single_chunk_text)
        if len(tokens) > MAX_CHUNK_TOKENS:
            logger.warning(f"Single sentence text is too long ({len(tokens)} tokens > {MAX_CHUNK_TOKENS}). Truncating or splitting further might be needed. For now, returning as one chunk.")
            # TODO: Implement splitting for single long sentences if this becomes an issue.
            # For now, we'll return it as is, but it might exceed embedding model limits.
        return [single_chunk_text]

    try:
        # Encode all sentences. Device management is handled by SentenceTransformer.
        sentence_embeddings = sentence_model.encode(sentences, show_progress_bar=False)
    except Exception as e:
        logger.error(f"Failed to encode sentences with SentenceTransformer: {e}. Returning text as a single chunk.", exc_info=True)
        return [text]


    chunks = []
    current_chunk_sentences_list = []
    current_token_count = 0

    for i in range(len(sentences)):
        sentence_text = sentences[i].strip()
        if not sentence_text:
            continue

        sentence_tokens = len(tokenizer.encode(sentence_text))
        
        # If current chunk is empty, always add the first sentence
        if not current_chunk_sentences_list:
            current_chunk_sentences_list.append(sentence_text)
            current_token_count = sentence_tokens
            continue

        # Calculate similarity with the *last added sentence* in the current chunk
        # This is slightly different from only adjacent: it checks if the new sentence fits the current forming chunk.
        # For simplicity and to match common patterns, let's stick to similarity with the direct previous sentence (sentences[i-1])
        # The sentence_embeddings correspond to the original sentences list.
        # The 'previous' sentence in the context of forming a chunk is the one at sentences[i-1]
        # if it was part of the current_chunk_sentences_list.
        # More directly: check similarity between sentences[i-1] and sentences[i]
        
        similarity_to_previous = util.cos_sim(sentence_embeddings[i-1], sentence_embeddings[i]).item()
        logger.debug(f"Similarity between \"{sentences[i-1][:50]}...\" and \"{sentences[i][:50]}...\" : {similarity_to_previous:.4f}")
        
        # Conditions to finalize the current chunk and start a new one:
        # 1. Semantic break: Similarity drops AND current chunk has reached a minimum sensible size.
        new_chunk_due_to_semantic_break = (similarity_to_previous < SEMANTIC_SIMILARITY_BREAK_THRESHOLD and 
                                           current_token_count >= MIN_CHUNK_TOKENS)
        
        # 2. Max token limit: Adding the current sentence would make the chunk too large.
        new_chunk_due_to_token_limit = (current_token_count + sentence_tokens) > MAX_CHUNK_TOKENS
        
        if new_chunk_due_to_semantic_break or new_chunk_due_to_token_limit:
            if current_chunk_sentences_list: # Ensure there's something to add
                chunks.append(" ".join(current_chunk_sentences_list))
                logger.debug(f"Created chunk (len: {current_token_count} tokens) due to: {'semantic break' if new_chunk_due_to_semantic_break else 'token limit'}")
            
            current_chunk_sentences_list = [sentence_text]
            current_token_count = sentence_tokens
        else:
            # Add current sentence to the ongoing chunk
            current_chunk_sentences_list.append(sentence_text)
            current_token_count += sentence_tokens

    # Add the last remaining chunk
    if current_chunk_sentences_list:
        chunks.append(" ".join(current_chunk_sentences_list))
        logger.debug(f"Created final chunk (len: {current_token_count} tokens)")

    # Filter out any empty chunks that might have been formed
    final_chunks = [chunk for chunk in chunks if chunk.strip()]
    logger.info(f"Semantic chunking produced {len(final_chunks)} chunks.")
    return final_chunks
# --- End Semantic Chunking Function ---

# Define the actual Celery task
# The name should match what's used in kb/api.py: process_kb_document_task.delay(...)
# If celery_app.py includes 'agent.tasks', this will be discoverable.
@celery_app.task(name='agent.tasks.process_kb_document_task', bind=True, acks_late=True, reject_on_worker_lost=True, default_retry_delay=5*60, max_retries=3)
async def process_kb_document_task(self, document_id: str):
    """
    Celery task to process a document for the Knowledge Base:
    1. Fetch document metadata.
    2. Download file from Supabase Storage.
    3. Extract text content.
    4. Chunk text.
    5. Generate embeddings for chunks.
    6. Store chunks and embeddings in the database.
    7. Update document status.
    """
    logger.info(f"[KB Task {self.request.id}] Starting processing for document_id: {document_id}")
    
    # It's generally better to create a new Supabase client per task or ensure thread-safety
    # if using a global client. For simplicity, re-creating or getting a fresh one.
    # This assumes get_supabase_client() can be called outside an HTTP request context,
    # or you have another way to initialize a Supabase client for background tasks.
    # If get_supabase_client relies on FastAPI Depends, we need a different way.
    # For now, let's assume a direct way to get a client, or adapt DBConnection.

    # Initialize services if needed (e.g., Supabase client)
    # Similar to worker_init_services but might be simpler if only Supabase is needed.
    db_connection = await get_db_connection() # From agent.run_utils or a similar utility
    db = await db_connection.client

    try:
        # 1. Fetch document metadata
        doc_query = await db.table("knowledge_base_documents").select("*").eq("id", document_id).maybe_single().execute()
        doc_info = doc_query.data

        if not doc_info:
            logger.error(f"[KB Task {self.request.id}] Document {document_id} not found. Aborting.")
            # No need to update status if doc doesn't exist, or could mark as 'error' if this is unexpected.
            return

        await _update_kb_document_status(db, document_id, "processing")
        
        file_name = doc_info.get("file_name")
        storage_path = doc_info.get("storage_path")
        mime_type = doc_info.get("mime_type")
        project_id = doc_info.get("project_id")
        bucket_name = "project_knowledge_bases" # As defined in kb/api.py

        logger.info(f"[KB Task {self.request.id}] Processing: {file_name} (type: {mime_type}) from storage path: {storage_path}")

        # 2. Download file from Supabase Storage
        file_content_bytes = None
        try:
            download_response = await asyncio.to_thread(db.storage.from_(bucket_name).download, storage_path)
            # download_response = db.storage.from_(bucket_name).download(storage_path) # Older sync version
            if not download_response:
                raise Exception("Downloaded content is empty or download failed.")
            file_content_bytes = download_response # This is already bytes
            logger.info(f"[KB Task {self.request.id}] Successfully downloaded {file_name} from storage.")
        except Exception as e_download:
            logger.error(f"[KB Task {self.request.id}] Failed to download {file_name} from {storage_path}: {e_download}")
            try:
                # Retry for generic download issues, which might be transient network problems.
                logger.info(f"[KB Task {self.request.id}] Retrying document download for {document_id} due to: {e_download}. Attempt {self.request.retries + 1} of {self.max_retries}")
                raise self.retry(exc=e_download, countdown=int(self.default_retry_delay * (self.request.retries + 1))) # Exponential backoff basic
            except self.MaxRetriesExceededError:
                logger.error(f"[KB Task {self.request.id}] Max retries exceeded for downloading {file_name}.")
                await _update_kb_document_status(db, document_id, "failed", f"Failed to download file after multiple retries: {str(e_download)[:200]}")
                return
            except Exception as e_retry_setup: # Catch issues with the retry call itself, though unlikely
                logger.error(f"[KB Task {self.request.id}] Error setting up retry for download: {e_retry_setup}. Failing document.")
                await _update_kb_document_status(db, document_id, "failed", f"Download error (retry mechanism failed): {str(e_download)[:180]}")
                return

        # 3. Extract text content
        extracted_text = ""
        try:
            if mime_type == "application/pdf":
                with io.BytesIO(file_content_bytes) as pdf_file:
                    reader = pypdf.PdfReader(pdf_file)
                    for page in reader.pages:
                        extracted_text += page.extract_text() + "\n"
            elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                with io.BytesIO(file_content_bytes) as docx_file:
                    document = docx.Document(docx_file)
                    for para in document.paragraphs:
                        extracted_text += para.text + "\n"
            elif mime_type in ["text/plain", "text/markdown"]:
                extracted_text = file_content_bytes.decode('utf-8') # Assuming UTF-8 for text files
            else:
                logger.warning(f"[KB Task {self.request.id}] Unsupported MIME type for text extraction: {mime_type} for file {file_name}")
                await _update_kb_document_status(db, document_id, "failed", f"Unsupported file type: {mime_type}")
                return
            logger.info(f"[KB Task {self.request.id}] Successfully extracted text from {file_name} (length: {len(extracted_text)} chars).")
        except pypdf_errors.PdfReadError as e_pdf_read:
            logger.error(f"[KB Task {self.request.id}] Failed to extract text from PDF {file_name} (corrupted or password-protected?): {e_pdf_read}")
            await _update_kb_document_status(db, document_id, "failed", f"PDF processing error: {str(e_pdf_read)[:200]}")
            return
        except Exception as e_extract: # General text extraction errors
            logger.error(f"[KB Task {self.request.id}] Failed to extract text from {file_name}: {e_extract}")
            await _update_kb_document_status(db, document_id, "failed", f"Text extraction failed: {str(e_extract)[:250]}")
            return

        if not extracted_text.strip():
            logger.warning(f"[KB Task {self.request.id}] Extracted text from {file_name} is empty. Marking as indexed (with no content).")
            await _update_kb_document_status(db, document_id, "indexed", "Document contained no extractable text.")
            return

        # 4. Chunk text
        # Using semantic chunking
        logger.info(f"[KB Task {self.request.id}] Starting semantic chunking for {file_name} (text length: {len(extracted_text)} chars).")
        if sentence_model is None:
            logger.error(f"[KB Task {self.request.id}] Sentence model not loaded. Semantic chunking cannot proceed for {file_name}. Document processing will fail.")
            await _update_kb_document_status(db, document_id, "failed", "Semantic chunking model unavailable.")
            return

        try:
            text_chunks = chunk_text_semantically(extracted_text)
        except Exception as e_chunk:
            logger.error(f"[KB Task {self.request.id}] Error during semantic chunking for {file_name}: {e_chunk}", exc_info=True)
            await _update_kb_document_status(db, document_id, "failed", f"Semantic chunking failed: {str(e_chunk)[:200]}")
            return
            
        logger.info(f"[KB Task {self.request.id}] Split text into {len(text_chunks)} chunks for {file_name} using semantic chunking.")

        if not text_chunks:
            logger.warning(f"[KB Task {self.request.id}] No text chunks generated for {file_name} despite non-empty text. This might be due to very short text.")
            await _update_kb_document_status(db, document_id, "indexed", "No text chunks generated (text might be too short).")
            return

        # 5. Generate embeddings for chunks and 6. Store chunks and embeddings
        # embedding_model = config.EMBEDDING_MODEL_TO_USE # Use sentence_model instead
        
        # Before inserting new chunks, delete existing ones
        try:
            await db.table("knowledge_base_chunks").delete().eq("document_id", document_id).execute()
            logger.info(f"[KB Task {self.request.id}] Deleted existing chunks for document {document_id} before re-indexing.")
        except Exception as e_delete_chunks:
            logger.error(f"[KB Task {self.request.id}] Failed to delete existing chunks for document {document_id}: {e_delete_chunks}")
            # Decide if this is a fatal error. For re-indexing, it might be.
            await _update_kb_document_status(db, document_id, "failed", f"Failed to clear old chunks: {str(e_delete_chunks)[:200]}")
            return

        chunk_num = 0
        # Prepare batch for embedding generation
        chunks_to_embed = []
        for chunk_text in text_chunks:
             if chunk_text.strip():
                 chunks_to_embed.append(chunk_text)
             else:
                 logger.debug(f"[KB Task {self.request.id}] Skipping empty chunk for document {document_id}")
        
        if not chunks_to_embed:
             logger.warning(f"[KB Task {self.request.id}] No non-empty chunks to embed for document {document_id}.")
             await _update_kb_document_status(db, document_id, "indexed", "Document contained no text after chunking.")
             return

        logger.info(f"[KB Task {self.request.id}] Generating embeddings for {len(chunks_to_embed)} non-empty chunks...")
        try:
            # --- CHANGE: Use local sentence_model instead of litellm.embedding ---
            if sentence_model is None:
                raise RuntimeError("Sentence model is not loaded, cannot generate embeddings.")
            embeddings = sentence_model.encode(chunks_to_embed, show_progress_bar=False).tolist()
            # --- END CHANGE ---
            
            if len(embeddings) != len(chunks_to_embed):
                raise ValueError(f"Mismatch between number of chunks ({len(chunks_to_embed)}) and embeddings ({len(embeddings)}).")
            logger.info(f"[KB Task {self.request.id}] Successfully generated {len(embeddings)} embeddings.")

        # --- CHANGE: Adjusted Exception handling for SentenceTransformer errors if any, removed LiteLLM specifics ---
        except Exception as e_embed:
            logger.error(f"[KB Task {self.request.id}] Embedding generation failed for doc {document_id}: {e_embed}", exc_info=True)
            # Decide on retry logic for SentenceTransformer errors
            try:
                raise self.retry(exc=e_embed, countdown=int(self.default_retry_delay * (self.request.retries + 1)))
            except self.MaxRetriesExceededError:
                 logger.error(f"[KB Task {self.request.id}] Max retries exceeded for embedding doc {document_id} after error: {e_embed}")
                 await _update_kb_document_status(db, document_id, "failed", f"Embedding failed after retries: {str(e_embed)[:150]}")
                 return
            except Exception as e_retry_setup:
                 logger.error(f"[KB Task {self.request.id}] Error setting up retry for embedding: {e_retry_setup}. Failing document.")
                 await _update_kb_document_status(db, document_id, "failed", f"Embedding error (retry setup failed): {str(e_embed)[:100]}")
                 return
        # --- END CHANGE ---
        
        # Store embeddings
        logger.info(f"[KB Task {self.request.id}] Storing {len(embeddings)} embeddings for document {document_id}...")
        chunks_to_insert = []
        processed_chunk_index = 0 # Index for embeddings list
        total_expected_chunks = len(text_chunks) # Original count including potential empty ones

        for chunk_num, chunk_text in enumerate(text_chunks): # Iterate original chunks
            if chunk_text.strip(): # Only store non-empty chunks
                # Construct metadata as used in the original function
                chunk_metadata = {
                    "source_document_id": document_id,
                    "file_name": file_name,
                    "project_id": str(project_id), 
                    "chunk_number": chunk_num + 1, # 1-based index
                    "original_text_length": len(chunk_text)
                }
                chunks_to_insert.append({
                    "document_id": document_id,
                    "chunk_text": chunk_text,
                    "embedding": embeddings[processed_chunk_index], # Get embedding from the dense list
                    "metadata": chunk_metadata
                })
                processed_chunk_index += 1
            # else: chunk was empty, skip insertion

        # Batch insert chunks
        if chunks_to_insert:
            # --- CHANGE: Use knowledge_base_chunks consistently --- 
            insert_embed_response = await db.table("knowledge_base_chunks").insert(chunks_to_insert).execute()
            # --- END CHANGE ---
            if not insert_embed_response.data:
                logger.error(f"[KB Task {self.request.id}] Failed to insert chunks for document {document_id}. Response: {insert_embed_response.error if hasattr(insert_embed_response, 'error') else 'No data returned'}")
                await _update_kb_document_status(db, document_id, "failed", "Failed to store chunks")
                # Potentially raise Exception to trigger retry?
                return # Stop processing
            logger.info(f"[KB Task {self.request.id}] Successfully stored {len(insert_embed_response.data)} chunks for document {document_id}.")
        else:
             logger.warning(f"[KB Task {self.request.id}] No non-empty chunks were available to store for document {document_id}.")
             # If no chunks were stored, the status might remain 'processing' or be set to 'indexed' (empty)
             await _update_kb_document_status(db, document_id, "indexed", "Document processed but yielded no storable chunks.")
             return

        # 7. Update document status to 'indexed' (assuming 'indexed' is the final success state)
        await _update_kb_document_status(db, document_id, "indexed") # Changed from completed to indexed
        logger.info(f"[KB Task {self.request.id}] Successfully completed processing for document_id: {document_id}")

    except Exception as e:
        logger.error(f"[KB Task {self.request.id}] Unhandled exception processing document {document_id}: {e}", exc_info=True)
        try:
            # Attempt to mark as failed if not already done
            await _update_kb_document_status(db, document_id, "failed", f"Unhandled task error: {str(e)[:250]}")
        except Exception as e_final_status:
            logger.error(f"[KB Task {self.request.id}] Critial: Failed to update final error status for doc {document_id}: {e_final_status}")
    finally:
        # Ensure DB connection is released/closed if it was task-specific
        # REMOVED: await db_connection.disconnect() - This is handled by worker lifecycle signals
        # if db_connection:
        #     await db_connection.disconnect() # Or your equivalent cleanup for the DB client
        logger.debug(f"[KB Task {self.request.id}] Finished processing for document_id: {document_id}. DB connection remains open for worker lifecycle.")

# Make sure this task is imported by Celery worker.
# If celery_app.py has `include=['agent.tasks']`, it should be picked up.

# ---- NEW TASK FOR URL PROCESSING ----
@celery_app.task(name="agent.tasks.process_kb_url_task", bind=True, acks_late=True, reject_on_worker_lost=True, default_retry_delay=5*60, max_retries=3)
async def process_kb_url_task(self, 
                              project_id_str: str, 
                              account_id_str: str, 
                              url: str, 
                              user_id_str: Optional[str] = None, # User ID might not be relevant for automated checks
                              existing_document_id_str: Optional[str] = None # Pass this for update checks
                             ):
    """
    Celery task to fetch, process, and embed content from a web URL for the Knowledge Base.
    If existing_document_id_str is provided, it checks for content changes before processing.
    """
    task_log_prefix = f"[Task {self.request.id}]"
    logger.info(f"{task_log_prefix} PROCESS_KB_URL_TASK starting. URL: {url}, Project: {project_id_str}, Existing Doc ID: {existing_document_id_str}")
    db_connection = None
    db_client = None
    document_id_to_process = None
    existing_doc_data = None

    try:
        # 1. Get Supabase Client
        db_connection = await get_db_connection()
        db_client = await db_connection.client
        logger.debug(f"{task_log_prefix} Supabase client obtained.")

        # 1b. Fetch existing document data if ID is provided (for updates)
        if existing_document_id_str:
            doc_query = await db_client.table("knowledge_base_documents")\
                                     .select("id, status, content_hash, source_url, file_name, project_id, account_id")\
                                     .eq("id", existing_document_id_str)\
                                     .maybe_single().execute()
            existing_doc_data = doc_query.data
            if not existing_doc_data:
                logger.warning(f"{task_log_prefix} Existing document ID {existing_document_id_str} provided but not found. Proceeding as new insert.")
                existing_document_id_str = None # Treat as new insert
            else:
                document_id_to_process = uuid.UUID(existing_document_id_str) # Use existing ID
                # Ensure project/account match if needed, though query implies ownership
                if str(existing_doc_data.get('project_id')) != project_id_str or \
                   str(existing_doc_data.get('account_id')) != account_id_str:
                    logger.error(f"{task_log_prefix} Mismatch between provided IDs and existing document {existing_document_id_str}. Aborting.")
                    # This case shouldn't happen if the scheduler task queries correctly.
                    return { "status": "error", "message": "Project/Account ID mismatch for update." }
                logger.info(f"{task_log_prefix} Found existing document {document_id_to_process}. Checking for updates.")

        # 2. Fetch and Extract URL Content using Trafilatura
        logger.info(f"{task_log_prefix} Fetching and extracting content from URL: {url}")
        downloaded_html = trafilatura.fetch_url(url)
        if not downloaded_html:
            # Log error but don't create DB record yet
            logger.error(f"{task_log_prefix} Trafilatura failed to fetch content for URL: {url}")
            # No document created yet, so just fail the task
            raise ValueError(f"Could not fetch content from URL: {url}")

        main_text = trafilatura.extract(downloaded_html, include_comments=False, include_tables=True, favor_recall=True)
        
        if not main_text or len(main_text.strip()) == 0:
            logger.warning(f"{task_log_prefix} Trafilatura could not extract main text content from URL: {url}")
            # No document created yet, fail the task
            raise ValueError(f"No main text extracted from URL: {url}")
        logger.info(f"{task_log_prefix} Successfully extracted text content (length: {len(main_text)}).")

        # 3. Calculate Hash of new content
        new_content_hash = hashlib.sha256(main_text.encode('utf-8')).hexdigest()
        logger.debug(f"{task_log_prefix} Calculated new content hash: {new_content_hash}")

        # 4. Check for Changes if updating existing document
        if existing_doc_data:
            old_content_hash = existing_doc_data.get('content_hash')
            # Get current time for updating timestamps
            now_iso = datetime.now(timezone.utc).isoformat()
            update_payload = { "last_checked_at": now_iso, "updated_at": now_iso }

            if old_content_hash == new_content_hash:
                logger.info(f"{task_log_prefix} Content hash matches for document {document_id_to_process}. No update needed.")
                # Only update last_checked_at and updated_at
                await db_client.table("knowledge_base_documents").update(update_payload).eq("id", str(document_id_to_process)).execute()
                return { "status": "unchanged", "document_id": str(document_id_to_process) }
            else:
                logger.info(f"{task_log_prefix} Content hash changed for document {document_id_to_process}. Proceeding with update.")
                # Update status to processing, set new hash and update timestamp
                update_payload["status"] = "processing"
                update_payload["content_hash"] = new_content_hash
                update_payload["file_size"] = len(main_text.encode('utf-8')) # Update size
                await db_client.table("knowledge_base_documents").update(update_payload).eq("id", str(document_id_to_process)).execute()
        else:
            # 5. Create New DB entry if not updating
            document_id_to_process = uuid.uuid4()
            file_name_from_url = url.split('/')[-1] or url.split('/')[-2] or url
            file_name_from_url = file_name_from_url.replace('=', '_').replace('?', '_').replace('&', '_') # More sanitization
            file_name_from_url = file_name_from_url[:250] 
            doc_metadata_for_url = { "source_url": url, "file_name": file_name_from_url }
            kb_doc_data = {
                "id": str(document_id_to_process),
                "project_id": project_id_str,
                "account_id": account_id_str,
                "file_name": file_name_from_url,
                "storage_path": url, 
                "source_url": url, 
                "file_size": len(main_text.encode('utf-8')),
                "mime_type": "text/webpage",
                "status": "processing", 
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "error_message": None,
                "metadata": doc_metadata_for_url, # Store metadata
                "content_hash": new_content_hash, # Store hash on creation
                "last_checked_at": datetime.now(timezone.utc).isoformat() # Set on creation too
            }
            insert_response = await db_client.table("knowledge_base_documents").insert(kb_doc_data).execute()
            if not insert_response.data:
                raise Exception("Failed to save URL document metadata to database.")
            logger.info(f"{task_log_prefix} Created DB entry {document_id_to_process} for URL {url}")

        # --- Processing Steps (Chunking, Embedding, Storing) ---
        # These steps run for both new documents and updated documents where hash changed.

        # 6. Chunk Text
        logger.info(f"{task_log_prefix} Chunking extracted text for document {document_id_to_process}...")
        text_chunks = chunk_text_semantically(main_text)
        if not text_chunks:
            logger.warning(f"{task_log_prefix} No text chunks generated for document {document_id_to_process}, URL: {url}")
            await _update_kb_document_status(db_client, str(document_id_to_process), "indexed", "Document contained no text after chunking.")
            return { "status": "completed_empty", "document_id": str(document_id_to_process) }
        logger.info(f"{task_log_prefix} Generated {len(text_chunks)} chunks for document {document_id_to_process}.")

        # 7. Generate Embeddings
        logger.info(f"{task_log_prefix} Generating embeddings for {len(text_chunks)} chunks using local SentenceTransformer...")
        try:
            if sentence_model is None: raise RuntimeError("Sentence model not loaded.")
            embeddings = sentence_model.encode(text_chunks, show_progress_bar=False).tolist()
            if not chunks_to_embed:
                 logger.warning(f"{task_log_prefix} No non-empty chunks to embed for document {document_id_to_process}.")
                 await _update_kb_document_status(db_client, str(document_id_to_process), "indexed", "Document contained no text after chunking.")
                 return { "status": "completed_empty", "document_id": str(document_id_to_process) }

            if len(embeddings) != len(text_chunks):
                 raise ValueError(f"Mismatch between number of chunks ({len(text_chunks)}) and embeddings ({len(embeddings)}).")
            logger.info(f"{task_log_prefix} Successfully generated {len(embeddings)} embeddings.")
        except Exception as e_embed: # Catch-all for embedding errors, including retry logic within if needed
             logger.error(f"{task_log_prefix} Embedding generation failed for doc {document_id_to_process}: {e_embed}", exc_info=True)
             await _update_kb_document_status(db_client, str(document_id_to_process), "failed", f"Embedding failed: {e_embed}")
             raise # Re-raise for Celery retry 

        # 8. Delete Old Chunks (Crucial for updates)
        logger.info(f"{task_log_prefix} Deleting existing chunks for document {document_id_to_process} before inserting new ones...")
        try:
            await db_client.table("knowledge_base_chunks").delete().eq("document_id", str(document_id_to_process)).execute()
        except Exception as e_delete_chunks:
            logger.error(f"{task_log_prefix} Failed to delete existing chunks for document {document_id_to_process}: {e_delete_chunks}")
            await _update_kb_document_status(db_client, str(document_id_to_process), "failed", f"Failed to clear old chunks: {str(e_delete_chunks)[:200]}")
            raise # Stop processing if old chunks can't be deleted
        logger.info(f"{task_log_prefix} Finished deleting old chunks for {document_id_to_process}.")

        # 9. Store New Embeddings
        logger.info(f"{task_log_prefix} Storing {len(embeddings)} new embeddings for document {document_id_to_process}...")
        chunks_to_insert = []
        processed_chunk_index = 0
        for chunk_num, chunk_text in enumerate(text_chunks):
            if chunk_text.strip():
                chunk_metadata = {
                    "source_document_id": str(document_id_to_process),
                    "source_url": url,
                    "file_name": existing_doc_data.get('file_name') if existing_doc_data else file_name_from_url, # Use existing name if updating
                    "project_id": project_id_str,
                    "chunk_number": chunk_num + 1,
                    "original_text_length": len(chunk_text)
                }
                chunks_to_insert.append({
                    "document_id": str(document_id_to_process),
                    "content": chunk_text,
                    "embedding": embeddings[processed_chunk_index],
                    "metadata": chunk_metadata
                })
                processed_chunk_index += 1
        
        if chunks_to_insert:
            insert_embed_response = await db_client.table("knowledge_base_chunks").insert(chunks_to_insert).execute()
            if not insert_embed_response.data:
                logger.error(f"{task_log_prefix} Failed to insert chunks for document {document_id_to_process}. Response: {insert_embed_response.error if hasattr(insert_embed_response, 'error') else 'No data returned'}")
                await _update_kb_document_status(db_client, str(document_id_to_process), "failed", "Failed to store chunks")
                return { "status": "failed", "document_id": str(document_id_to_process) }
            logger.info(f"{task_log_prefix} Successfully stored {len(insert_embed_response.data)} new chunks for document {document_id_to_process}.")
        else:
            logger.warning(f"{task_log_prefix} No non-empty chunks were generated/available to store for document {document_id_to_process}.")
            # Status might already be 'processing' if update, or just created.
            # Set to 'indexed' but note it's empty.
            await _update_kb_document_status(db_client, str(document_id_to_process), "indexed", "Processed but yielded no storable chunks.")
            return { "status": "completed_empty", "document_id": str(document_id_to_process) }

        # 10. Update Status to Completed ('indexed')
        await _update_kb_document_status(db_client, str(document_id_to_process), "indexed")
        logger.info(f"{task_log_prefix} Successfully processed URL {url}. Final status 'indexed' for document {document_id_to_process}")
        return {"status": "indexed", "document_id": str(document_id_to_process)}

    except Exception as e:
        task_id = self.request.id
        logger.error(f"{task_log_prefix} UNHANDLED EXCEPTION processing URL {url} (Doc ID: {document_id_to_process if document_id_to_process else 'N/A'}): {e}", exc_info=True)
        if document_id_to_process and db_client:
            await _update_kb_document_status(db_client, str(document_id_to_process), "failed", str(e))
        raise # Re-raise for Celery
    finally:
        # Clean up DB connection if necessary (depends on how get_db_connection manages connections)
        if db_connection:
            # Assuming your DBConnection class has a disconnect or cleanup method
            # await db_connection.disconnect() # Or similar cleanup
            logger.debug(f"{task_log_prefix} DB Connection cleanup placeholder.")
        logger.info(f"{task_log_prefix} PROCESS_KB_URL_TASK finished for URL: {url}")

# ---- END MODIFIED TASK ----

# ---- PERIODIC TASK FOR CHECKING KB UPDATES ----
@celery_app.task(name="agent.tasks.check_kb_updates_task")
async def check_kb_updates_task():
    """
    Periodically checks knowledge base documents sourced from URLs for updates.
    Finds documents that haven't been checked recently and queues them for processing.
    """
    logger.info("Starting periodic KB update check...")
    db_connection = None
    db_client = None
    # Define check frequency (e.g., check docs older than 1 day)
    check_interval = timedelta(days=1) # Requires: from datetime import timedelta
    check_before_time = datetime.now(timezone.utc) - check_interval

    try:
        db_connection = await get_db_connection()
        db_client = await db_connection.client

        # Query for URL documents that are indexed and haven't been checked recently
        # Note: Assumes newly added URLs have last_checked_at set initially.
        # We query for status='indexed' to avoid rechecking failed or pending docs.
        query = db_client.table("knowledge_base_documents")\
            .select("id, project_id, account_id, source_url")\
            .eq("status", "indexed")\
            .eq("mime_type", "text/webpage")\
            .is_("source_url", "not.null")\
            .lt("last_checked_at", check_before_time.isoformat())
            # Add limit to avoid overwhelming the system
            # .limit(100) 
        
        # Alternatively, query based on an update_frequency column if added
        # query = query.eq("update_frequency", "daily") # Example

        response = await query.execute()

        if response.data is None:
            logger.info("No URL documents found requiring an update check.")
            return {"status": "no_docs_to_check"}
        
        docs_to_check = response.data
        logger.info(f"Found {len(docs_to_check)} URL documents to check for updates.")

        queued_count = 0
        for doc in docs_to_check:
            doc_id = doc.get('id')
            project_id = doc.get('project_id')
            account_id = doc.get('account_id')
            url_to_check = doc.get('source_url') # Use source_url field

            if not all([doc_id, project_id, account_id, url_to_check]):
                logger.warning(f"Skipping document check due to missing data: {doc}")
                continue

            # Queue the processing task, passing the existing document ID
            try:
                process_kb_url_task.delay(
                    project_id_str=str(project_id),
                    account_id_str=str(account_id),
                    url=url_to_check,
                    user_id_str=None, # No specific user triggered this
                    existing_document_id_str=str(doc_id)
                )
                queued_count += 1
                logger.info(f"Queued update check for document {doc_id} (URL: {url_to_check})")
                # Optional: Add a small delay between queuing tasks
                # await asyncio.sleep(0.1)
            except Exception as e_queue:
                 logger.error(f"Failed to queue update task for document {doc_id}: {e_queue}")

        logger.info(f"Finished KB update check. Queued {queued_count} documents for processing.")
        return {"status": "completed", "checked_count": len(docs_to_check), "queued_count": queued_count}

    except Exception as e:
        logger.error(f"Error during periodic KB update check: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        # Connection cleanup handled by worker lifecycle
        logger.debug("KB Update Check: DB connection cleanup placeholder.")
        logger.info("Periodic KB update check finished.")

# ---- END PERIODIC TASK ----
