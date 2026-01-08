from .extensions import NewelleExtension
from .tools import create_io_tool
from .utility.system import get_spawn_command, is_flatpak
from .handlers.extra_settings import ExtraSettings
import subprocess
import os
import glob
import shutil
import requests
import threading
import json
from .tools import ToolResult, Tool
from gi.repository import GLib, Gtk, GtkSource, Pango


class AdvancedToolsExtension(NewelleExtension):
    name = "Advanced Tools"
    id = "advanced_tools"

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.ScaleSetting("max_output_length", "Max Output Length", "Max length of the output in characters", 5000, 1000, 100000, 0),
            ExtraSettings.ToggleSetting("secondary_llm", "Secondary LLM for image analysis", "Use the secondary LLM to analyze images", True),
            ExtraSettings.MultilineEntrySetting("image_analysis_prompt", "Image Analysis Prompt", "Prompt to analyze the image", "Analyze the image and return the information in the image"),
        ]

    def _truncate(self, text: str) -> str:
        maxlength = self.get_setting("max_output_length")
        if len(text) > maxlength:
            return text[:maxlength] + f"\n... (Output truncated to {maxlength} characters)"
        return text

    def analyze_image(self, image_path: str, query: str):
        llm = None
        if self.get_setting("secondary_llm"):
            if self.secondary_llm.supports_vision():
                llm = self.secondary_llm 
            elif self.primary_llm.supports_vision():
                llm = self.llm 
        else:
            if self.llm.supports_vision():
                llm = self.llm 
        if llm is None:
            return "No LLM supports vision"
        query = "```image```\n" + image_path + "\n```"
        query += "\n" + query

        return llm.generate_text(query, [], [self.get_setting("image_analysis_prompt")])

    def semantic_search(self, documents: list[str], query: str, chunk_size: int = 1024):
        index = self.rag.build_index(documents, chunk_size)
        return self._truncate("\n\n".join(index.query(query)))

    def grep(self, pattern: str, path: str = ".", recursive: bool = True):
        # Using system grep for speed as requested
        cmd_list = ["grep", "-n"] # -n for line numbers
        if recursive:
            cmd_list.append("-r")
        
        # Add basic options to handle binary files and exclude dirs if needed? 
        # For now, keep it simple.
        
        cmd_list.extend([pattern, path])
        
        full_cmd = get_spawn_command() + cmd_list
        
        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                return self._truncate(result.stdout)
            elif result.returncode == 1:
                return "No matches found."
            else:
                return f"Grep error: {result.stderr}"
        except Exception as e:
            return f"Error running grep: {str(e)}"

    def delete_file(self, file_path: str):
        try:
            if os.path.isdir(file_path):
                # Using rmdir for safety, only empty directories
                os.rmdir(file_path)
            else:
                os.remove(file_path)
            return f"Successfully deleted {file_path}"
        except Exception as e:
            return f"Error deleting file: {str(e)}"

    def search_replace(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if old_string not in content:
                return f"String '{old_string}' not found in {file_path}"
            
            if replace_all:
                new_content = content.replace(old_string, new_string)
            else:
                new_content = content.replace(old_string, new_string, 1)
                
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
                
            return f"Successfully replaced text in {file_path}"
        except Exception as e:
            return f"Error performing search replace: {str(e)}"

    def change_directory(self, directory_path: str):
        try:
            os.chdir(directory_path)
            self.ui_controller.new_explorer_tab(directory_path, False)
            return f"Successfully changed directory to {directory_path}"
        except Exception as e:
            return f"Error changing directory: {str(e)}"

    def write(self, file_path: str, contents: str, start_line: int = None):
        try:
            if start_line is None:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(contents)
                return f"Successfully wrote to {file_path}"

            if not os.path.exists(file_path):
                if start_line == 1:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(contents)
                    return f"Successfully wrote to {file_path}"
                return f"Error: File {file_path} does not exist and start_line {start_line} > 1"

            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            if start_line < 1:
                return "Error: start_line must be >= 1"

            while len(lines) < start_line - 1:
                lines.append("\n")

            new_lines = contents.splitlines(keepends=True)
            if contents and not new_lines:
                new_lines = [contents]
            elif not contents:
                new_lines = []

            start_idx = start_line - 1
            lines[start_idx:start_idx + len(new_lines)] = new_lines

            with open(file_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)

            return f"Successfully wrote to {file_path}"
        except Exception as e:
            return f"Error writing file: {str(e)}"

    def read_file(self, file_path: str, start_line: int = None, end_line: int = None, show_line_numbers: bool = False):
        try:
            if start_line is None and end_line is None and not show_line_numbers:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    return self._truncate(content)

            lines_output = []
            current_line_idx = 1
            start_val = start_line if start_line is not None else 1
            
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if end_line is not None and current_line_idx > end_line:
                        break
                    
                    if current_line_idx >= start_val:
                        if show_line_numbers:
                            lines_output.append(f"{current_line_idx}| {line}")
                        else:
                            lines_output.append(line)
                    
                    current_line_idx += 1

            content = "".join(lines_output)
            return self._truncate(content)
        except Exception as e:
            return f"Error reading file: {str(e)}"

    def list_dir(self, target_directory: str = "."):
        try:
            items = os.listdir(target_directory)
            return self._truncate("\n".join(items))
        except Exception as e:
            return f"Error listing directory: {str(e)}"

    def glob_file_search(self, glob_pattern: str, target_directory: str = "."):
        try:
            search_path = os.path.join(target_directory, glob_pattern)
            files = glob.glob(search_path, recursive=True)
            return self._truncate("\n".join(files))
        except Exception as e:
            return f"Error searching files: {str(e)}"

    def create_directory(self, directory_path: str):
        try:
            os.makedirs(directory_path, exist_ok=True)
            return f"Successfully created directory {directory_path}"
        except Exception as e:
            return f"Error creating directory: {str(e)}"

    def copy_file(self, source_path: str, destination_path: str):
        try:
            shutil.copy2(source_path, destination_path)
            return f"Successfully copied {source_path} to {destination_path}"
        except Exception as e:
            return f"Error copying file: {str(e)}"

    def rename_file(self, source_path: str, destination_path: str):
        try:
            shutil.move(source_path, destination_path)
            return f"Successfully renamed/moved {source_path} to {destination_path}"
        except Exception as e:
            return f"Error renaming/moving file: {str(e)}"

    def download_file(self, url: str, destination_path: str):
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            with open(destination_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return f"Successfully downloaded {url} to {destination_path}"
        except Exception as e:
            return f"Error downloading file: {str(e)}"

    def get_tools(self):
        return [
            create_io_tool("semantic_search", "Run semantic search on the given docuemnts, that can be a list of files, folders or websites urls", self.semantic_search, title="Semantic Search"),
            create_io_tool("grep", "Search for exact text or regex patterns in files (faster than semantic search for specific symbols).", self.grep, title="Grep"),
            create_io_tool("delete_file", "Delete files from the filesystem.", self.delete_file, title="Delete File"),
            create_io_tool("search_replace", "targeted text replacement within a file.", self.search_replace, title="Search and Replace"),
            create_io_tool("write", "Create or overwrite files. If start_line is provided, overwrites starting from that line.", self.write, title="Write File"),
            create_io_tool("read_file", "Read the contents of files. Supports optional start_line, end_line, and show_line_numbers.", self.read_file, title="Read File"),
            create_io_tool("list_dir", "List files and directories in a specific path.", self.list_dir, title="List Directory"),
            create_io_tool("change_directory", "Change the current directory.", self.change_directory, title="Change Directory"),
            create_io_tool("glob_file_search", "Find files matching specific name patterns (globbing).", self.glob_file_search, title="Glob File Search"),
            create_io_tool("create_directory", "Create a new directory (and parent directories if needed).", self.create_directory, title="Create Directory"),
            create_io_tool("copy_file", "Copy a file from source to destination.", self.copy_file, title="Copy File"),
            create_io_tool("rename_file", "Rename a file. This tool can also be used to move a file.", self.rename_file, title="Rename/Move File"),
            create_io_tool("download_file", "Download a file from a URL to a destination path.", self.download_file, title="Download File"),
            create_io_tool("analyze_image", "Analyze an image and return the information in the image", self.analyze_image, title="Analyze Image"),
        ]
