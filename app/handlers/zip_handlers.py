"""Handlers for ZIP file operations: import, generation, and comparison."""
from aiogram import Router, F
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import get_session
from app.services.memory import get_active_project
from app.services.artifacts import create_import, get_or_create_project
from app.storage import save_file, load_file
from app.utils.zip_utils import (
    extract_text_files, make_zip, diff_archives, 
    validate_zip_file, get_file_stats
)
from app.llm import generate_zip_files, generate_single_file, analyze_diff_context
from app.services.memory import gather_context, list_artifacts
from app.tokenizer import make_chunks, count_tokens
import logging
import tempfile
from typing import List, Optional
from html import escape

logger = logging.getLogger(__name__)
router = Router()

@router.message(Command("importzip"))
async def import_zip_hint(message: Message):
    """Show help for importzip command."""
    await message.answer(
        "[ZIP] Import ZIP archive\n\n"
        "Send a ZIP file, then reply with command:\n"
        "/importzip tags python,backend\n\n"
        "Bot will extract text files (.py, .js, .md, .json, .sql etc.) "
        "and save them as artifacts with specified tags."
    )

@router.message(Command("importzip"), F.reply_to_message)
async def import_zip_archive(message: Message, session: AsyncSession = get_session()):
    """Import ZIP archive and extract text files."""
    try:
        st = await anext(session)
        proj = await get_active_project(st, message.from_user.id)
        if not proj:
            await message.answer("First select a project: /project <name>")
            return
            
        if not message.reply_to_message or not message.reply_to_message.document:
            await message.answer("Need to reply with /importzip command to a message with ZIP file")
            return
            
        doc = message.reply_to_message.document
        
        # Check if it's a ZIP file
        if not doc.file_name or not doc.file_name.lower().endswith('.zip'):
            await message.answer("Only ZIP files are supported")
            return
            
        await message.answer("Processing ZIP archive...")
        
        # Parse tags from command
        tags = []
        if message.text:
            parts = message.text.split("tags", 1)
            if len(parts) > 1:
                tags = [t.strip() for t in parts[1].split(',') if t.strip()]
        
        # Download ZIP file
        if not message.bot:
            await message.answer("Bot access error")
            return
            
        file = await message.bot.get_file(doc.file_id)
        if not file.file_path:
            await message.answer("Could not get file path")
            return
            
        file_bytes_io = await message.bot.download_file(file.file_path)
        if not file_bytes_io:
            await message.answer("Could not download file")
            return
            
        zip_data = file_bytes_io.read()
        
        # Validate ZIP file
        is_valid, error_msg = validate_zip_file(zip_data)
        if not is_valid:
            await message.answer(f"Error: {error_msg}")
            return
            
        # Get file statistics
        stats = get_file_stats(zip_data)
        
        # Save ZIP to MinIO as blob
        zip_uri = await save_file(doc.file_name, zip_data)
        
        # Create blob artifact for the ZIP file itself
        blob_tags = tags + ['zip', 'blob']
        await create_import(
            st, proj,
            title=f"ZIP Archive: {escape(doc.file_name)}",
            text=f"ZIP archive with {stats['total_files']} files. Text files: {stats['text_files']}, Binary files: {stats['binary_files']}",
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
            tags=blob_tags,
            uri=zip_uri
        )
        
        # Extract text files
        text_files = extract_text_files(zip_data)
        
        if not text_files:
            await message.answer("No processable text files found in archive")
            return
            
        # Process each text file as separate artifact
        processed_count = 0
        for file_path, content in text_files.items():
            try:
                # Determine tags for this file
                file_tags = tags.copy()
                file_ext = file_path.split('.')[-1].lower() if '.' in file_path else 'txt'
                if file_ext not in file_tags:
                    file_tags.append(file_ext)
                file_tags.append('extracted')
                
                # Create artifact for the file
                await create_import(
                    st, proj,
                    title=f"File: {file_path}",
                    text=content,
                    chunk_size=settings.chunk_size,
                    overlap=settings.chunk_overlap,
                    tags=file_tags,
                    uri=None  # File content is in raw_text
                )
                processed_count += 1
                
            except Exception as e:
                logger.error(f"Error processing file {file_path}: {e}")
                continue
                
        await st.commit()
        
        # Send summary
        tags_str = ', '.join(tags) if tags else 'none'
        await message.answer(
            f"ZIP archive imported to project <b>{escape(proj.name)}</b>\n\n"
            f"Archive: {escape(doc.file_name)}\n"
            f"Files processed: {processed_count} of {stats['text_files']} text files\n"
            f"Tags: {tags_str}\n"
            f"Archive URI: {zip_uri or '—'}\n\n"
            f"Statistics:\n"
            f"  • Total files: {stats['total_files']}\n"
            f"  • Text files: {stats['text_files']}\n"
            f"  • Binary files: {stats['binary_files']}"
        )
        
    except Exception as e:
        logger.error(f"Error importing ZIP: {e}")
        await message.answer(f"Error importing ZIP: {str(e)}")

@router.message(Command("genzip"))
async def generate_zip_archive(message: Message, session: AsyncSession = get_session()):
    """Generate ZIP archive using AI based on task description."""
    try:
        st = await anext(session)
        proj = await get_active_project(st, message.from_user.id)
        if not proj:
            await message.answer("First select a project: /project <name>")
            return
            
        # Parse command: /genzip "description" tags tag1,tag2
        if not message.text:
            await message.answer("Command text not found")
            return
            
        command_parts = message.text.split('"')
        if len(command_parts) < 3:
            await message.answer(
                "Command format:\n"
                '/genzip "task description" tags python,backend\n\n'
                "Example:\n"
                '/genzip "Create user API" tags api,users'
            )
            return
            
        task_description = command_parts[1].strip()
        
        # Parse tags
        tags = []
        remaining_text = command_parts[2] if len(command_parts) > 2 else ""
        if "tags" in remaining_text:
            tags_part = remaining_text.split("tags", 1)[1].strip()
            tags = [t.strip() for t in tags_part.split(',') if t.strip()]
            
        await message.answer("Generating archive files...")
        
        # Gather project context
        context_chunks = await gather_context(st, proj, user_id=message.from_user.id, max_chunks=settings.project_max_chunks)
        
        # Generate files using AI
        generated_files = await generate_zip_files(task_description, context_chunks, tags)
        
        if not generated_files:
            await message.answer("Failed to generate files")
            return
            
        # Create ZIP archive
        zip_data = make_zip(generated_files)
        
        # Save ZIP to MinIO
        zip_filename = f"generated_{escape(proj.name)}_{len(generated_files)}_files.zip"
        zip_uri = await save_file(zip_filename, zip_data)
        
        # Create artifact for generated ZIP
        gen_tags = tags + ['generated', 'genzip']
        files_list = "\n".join([f"  • {path}" for path in generated_files.keys()])
        
        await create_import(
            st, proj,
            title=f"Generated Archive: {task_description}",
            text=f"AI-generated archive for: {task_description}\n\nFiles:\n{files_list}",
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
            tags=gen_tags,
            uri=zip_uri
        )
        
        await st.commit()
        
        # Send ZIP file as document
        zip_file = BufferedInputFile(zip_data, filename=zip_filename)
        
        tags_str = ', '.join(tags) if tags else 'none'
        caption = (
            f"Generated archive for project <b>{escape(proj.name)}</b>\n\n"
            f"Task: {escape(task_description)}\n"
            f"Files: {len(generated_files)}\n"
            f"Tags: {tags_str}\n"
            f"URI: {zip_uri}\n\n"
            f"Files:\n{files_list}"
        )
        
        await message.answer_document(zip_file, caption=caption)
        
    except Exception as e:
        logger.error(f"Error generating ZIP: {e}")
        await message.answer(f"Error generating ZIP: {str(e)}")

@router.message(Command("genfile"))
async def generate_single_file_handler(message: Message, session: AsyncSession = get_session()):
    """Generate a single file using AI."""
    try:
        st = await anext(session)
        proj = await get_active_project(st, message.from_user.id)
        if not proj:
            await message.answer("First select a project: /project <name>")
            return
            
        # Parse command: /genfile path=app/module/file.py "description"
        if not message.text:
            await message.answer("Command text not found")
            return
            
        if "path=" not in message.text or '"' not in message.text:
            await message.answer(
                "Command format:\n"
                '/genfile path=app/module/file.py "description"\n\n'
                "Example:\n"
                '/genfile path=src/auth/login.py "Add two-factor authentication"'
            )
            return
            
        # Extract path
        path_part = message.text.split("path=")[1].split('"')[0].strip()
        file_path = path_part
        
        # Extract description
        desc_parts = message.text.split('"')
        if len(desc_parts) < 2:
            await message.answer("Please specify description in quotes")
            return
            
        task_description = desc_parts[1].strip()
        
        await message.answer(f"Generating file {file_path}...")
        
        # Gather project context
        context_chunks = await gather_context(st, proj, user_id=message.from_user.id, max_chunks=settings.project_max_chunks)
        
        # Generate file content using AI
        file_content = await generate_single_file(file_path, task_description, context_chunks)
        
        # Save file to MinIO
        file_uri = await save_file(file_path.split('/')[-1], file_content.encode('utf-8'))
        
        # Create artifact for generated file
        file_ext = file_path.split('.')[-1].lower() if '.' in file_path else 'txt'
        gen_tags = [file_ext, 'generated', 'genfile']
        
        await create_import(
            st, proj,
            title=f"Generated File: {escape(file_path)}",
            text=file_content,
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
            tags=gen_tags,
            uri=file_uri
        )
        
        await st.commit()
        
        # Send file as document
        file_doc = BufferedInputFile(
            file_content.encode('utf-8'), 
            filename=file_path.split('/')[-1]
        )
        
        caption = (
            f"Generated file for project <b>{escape(proj.name)}</b>\n\n"
            f"Path: {escape(file_path)}\n"
            f"Task: {escape(task_description)}\n"
            f"Size: {len(file_content)} characters\n"
            f"URI: {file_uri}"
        )
        
        await message.answer_document(file_doc, caption=caption)
        
    except Exception as e:
        logger.error(f"Error generating file: {e}")
        await message.answer(f"Error generating file: {str(e)}")

@router.message(Command("diffzip"), F.reply_to_message)
async def diff_zip_archives(message: Message, session: AsyncSession = get_session()):
    """Compare ZIP archives and show differences."""
    try:
        st = await anext(session)
        proj = await get_active_project(st, message.from_user.id)
        if not proj:
            await message.answer("First select a project: /project <name>")
            return
            
        if not message.reply_to_message or not message.reply_to_message.document:
            await message.answer("Need to reply with /diffzip command to a message with ZIP file")
            return
            
        doc = message.reply_to_message.document
        if not doc or not doc.file_name or not doc.file_name.lower().endswith('.zip'):
            await message.answer("Need to reply with /diffzip command to a message with ZIP file")
            return
            
        await message.answer("Comparing archives...")
        
        # Download new ZIP
        if not message.bot:
            await message.answer("Bot access error")
            return
            
        file = await message.bot.get_file(doc.file_id)
        if not file.file_path:
            await message.answer("Could not get file path")
            return
            
        file_bytes_io = await message.bot.download_file(file.file_path)
        if not file_bytes_io:
            await message.answer("Could not download file")
            return
            
        new_zip_data = file_bytes_io.read()
        
        # Validate new ZIP
        is_valid, error_msg = validate_zip_file(new_zip_data)
        if not is_valid:
            await message.answer(f"Error: {error_msg}")
            return
            
        # Find the latest ZIP/snapshot artifact
        artifacts = await list_artifacts(st, proj, kinds={'importzip', 'snapshot', 'blob'})
        latest_zip_artifact = None
        
        for artifact in artifacts:
            if artifact.uri and (artifact.tags and 'zip' in artifact.tags):
                latest_zip_artifact = artifact
                break
                
        if not latest_zip_artifact or not latest_zip_artifact.uri:
            await message.answer("No previous archive found for comparison")
            return
            
        # Download old ZIP from MinIO
        old_zip_key = latest_zip_artifact.uri.split('/')[-1]  # Extract key from URI
        old_zip_data = await load_file(old_zip_key)
        
        if not old_zip_data:
            await message.answer("Could not load previous archive")
            return
            
        # Generate diff
        summary, diff_details = diff_archives(old_zip_data, new_zip_data)
        
        # Create full diff text
        full_diff = f"Comparison between {escape(latest_zip_artifact.title)} and {escape(doc.file_name)}\n\n"
        full_diff += summary + "\n\n"
        full_diff += "DETAILED DIFF:\n" + "="*50 + "\n"
        
        for file_path, file_diff in diff_details.items():
            full_diff += f"\n\nFILE: {file_path}\n"
            full_diff += "-" * 40 + "\n"
            full_diff += file_diff
            
        # Save diff to MinIO
        diff_filename = f"diff_{escape(proj.name)}_{escape(doc.file_name)}.txt"
        diff_uri = await save_file(diff_filename, full_diff.encode('utf-8'))
        
        # Gather context for analysis
        context_chunks = await gather_context(st, proj, user_id=message.from_user.id, max_chunks=20)
        
        # Get AI analysis
        analysis = await analyze_diff_context(summary, context_chunks)
        
        # Create diff artifact
        await create_import(
            st, proj,
            title=f"Diff: {escape(latest_zip_artifact.title)} vs {escape(doc.file_name)}",
            text=full_diff[:10000] + ("..." if len(full_diff) > 10000 else ""),  # Truncate for storage
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
            tags=['diff', 'comparison'],
            uri=diff_uri
        )
        
        await st.commit()
        
        # Send summary and analysis
        response = f"Archive comparison <b>Results</b>\n\n{summary}\n\n{analysis}\n\nFull diff: {diff_uri}"
        
        # Split long messages
        if len(response) > 4000:
            await message.answer(f"Archive comparison <b>Results</b>\n\n{summary}")
            await message.answer(analysis)
            await message.answer(f"Full diff: {diff_uri}")
        else:
            await message.answer(response)
            
        # Send diff file if not too large
        if len(full_diff.encode('utf-8')) < 10 * 1024 * 1024:  # 10MB limit
            diff_doc = BufferedInputFile(
                full_diff.encode('utf-8'),
                filename=diff_filename
            )
            await message.answer_document(
                diff_doc,
                caption="Full diff between archives"
            )
        
    except Exception as e:
        logger.error(f"Error comparing ZIP archives: {e}")
        await message.answer(f"Error comparing archives: {str(e)}")