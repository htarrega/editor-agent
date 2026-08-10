import os

from .base import ToolDefinition


def read_file(args):
    try:
        with open(args["path"], "r", encoding="utf-8") as f:
            return f.read(), None
    except Exception as e:
        return "", str(e)


read_file_definition = ToolDefinition(
    name="read_file",
    description=(
        "Read the contents of a file at a given relative or absolute path. Use this when "
        "you want to see what is inside a file. Do not use it on directories."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path of a file in the working directory.",
            }
        },
        "required": ["path"],
    },
    function=read_file,
)


def list_files(args):
    path = args.get("path") or "."
    try:
        entries = []
        with os.scandir(path) as it:
            for entry in it:
                name = entry.name + "/" if entry.is_dir() else entry.name
                entries.append(name if path == "." else os.path.join(path, name))
        if not entries:
            return "(empty directory)", None
        return "\n".join(sorted(entries)), None
    except Exception as e:
        return "", str(e)


list_files_definition = ToolDefinition(
    name="list_files",
    description=(
        "List files and directories directly under a given path, one level only. "
        "If no path is given, lists the current directory. Directories end with "
        "'/' - list one again to see what is inside it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Optional relative path. Defaults to the current directory.",
            }
        },
        "required": [],
    },
    function=list_files,
)


def edit_file(args):
    path = args["path"]
    old_str = args["old_str"]
    new_str = args["new_str"]

    if not path or old_str == new_str:
        return "", "invalid input parameters"

    try:
        if not os.path.exists(path) and old_str == "":
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_str)
            return f"Successfully created file {path}", None

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        if old_str and old_str not in content:
            return "", "old_str not found in file"

        with open(path, "w", encoding="utf-8") as f:
            f.write(content.replace(old_str, new_str))
        return "OK", None
    except Exception as e:
        return "", str(e)


edit_file_definition = ToolDefinition(
    name="edit_file",
    description=(
        "Make edits to a text file.\n\n"
        "Replaces 'old_str' with 'new_str' in the given file. 'old_str' and "
        "'new_str' MUST be different from each other.\n\n"
        "If the file at 'path' does not exist, it will be created."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The path to the file"},
            "old_str": {
                "type": "string",
                "description": "Text to search for - must match exactly and appear only once",
            },
            "new_str": {
                "type": "string",
                "description": "Text to replace old_str with",
            },
        },
        "required": ["path", "old_str", "new_str"],
    },
    function=edit_file,
)


FILE_TOOLS = [
    read_file_definition,
    list_files_definition,
    edit_file_definition,
]
