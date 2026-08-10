import os
from openai import OpenAI
import json

MODEL = "deepseek-v4-flash"
RESPONSE_MAX_TOKENS = 32768




class ToolDefinition:
    def __init__(self, name, description, parameters, function):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.function = function

    def to_openai(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


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
        files = []
        for root, dirs, filenames in os.walk(path):
            dirs[:] = [d for d in dirs if d not in {".git", "venv", "__pycache__"}]
            rel_root = os.path.relpath(root, path)
            if rel_root != ".":
                files.append(rel_root + "/")
            for name in filenames:
                files.append(name if rel_root == "." else os.path.join(rel_root, name))
        return json.dumps(files), None
    except Exception as e:
        return "", str(e)


list_files_definition = ToolDefinition(
    name="list_files",
    description=(
        "List files and directories at a given path. If no path is given, "
        "lists the current directory."
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

class Agent:
    def __init__(self, client, get_user_message, tools=None):
        self.client = client
        self.get_user_message = get_user_message
        self.tools = tools or []

    def run(self):
        conversation = []
        print("Chat with DeepSeek lil agent (ctrl-d to quit)")
        read_user_input = True

        while True:
            if read_user_input:
                print("\033[94mYou\033[0m: ", end="", flush=True)
                user_input, ok = self.get_user_message()
                if not ok:
                    break
                conversation.append({"role": "user", "content": user_input})

            message,finish_reason = self.run_inference(conversation)
            dumped = message.model_dump(exclude_none=True)
            dumped.pop("reasoning_content", None)
            dumped.setdefault("content", "")
            conversation.append(dumped)
           

            if finish_reason == "length":
                print("\033[91m[aviso]\033[0m Response truncated by reaching max_tokens ")
                read_user_input = True
                continue

            if message.content:
                print(f"\033[93mDeepSeek\033[0m: {message.content}")

            tool_calls = message.tool_calls or []
            if not tool_calls:
                read_user_input = True
                continue

            for call in tool_calls:
                conversation.append(self.execute_tool(call))
            read_user_input = False

    def execute_tool(self, call):
        name = call.function.name
        raw_args = call.function.arguments or "{}"

        for tool in self.tools:
            if tool.name == name:
                print(f"\033[92mtool\033[0m: {name}({raw_args})")
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError as e:
                    result, error = "", f"invalid JSON arguments: {e}"
                else:
                    result, error = tool.function(args)
                content = f"error: {error}" if error else result
                return {"role": "tool", "tool_call_id": call.id, "content": content}

        return {
            "role": "tool",
            "tool_call_id": call.id,
            "content": "error: tool not found",
        }

    def run_inference(self, conversation):
        response = self.client.chat.completions.create(
            model=MODEL,
            max_tokens=RESPONSE_MAX_TOKENS,
            messages=conversation,
            tools=[tool.to_openai() for tool in self.tools] or None,
            )
        choice = response.choices[0]
        print(f"\033[90m[debug] finish_reason={choice.finish_reason} usage={response.usage}\033[0m")
        return choice.message, choice.finish_reason


def main():
    client = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
    )

    def get_user_message():
        try:
            return input(), True
        except EOFError:
            return "", False

    tools = [read_file_definition, list_files_definition,edit_file_definition]
    Agent(client, get_user_message, tools).run()


if __name__ == "__main__":
    main()