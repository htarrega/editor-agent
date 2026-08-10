import os
import json

from openai import OpenAI
from tools import ALL_TOOLS


MODEL = "deepseek-v4-flash"
RESPONSE_MAX_TOKENS = 32768


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

    Agent(client, get_user_message, ALL_TOOLS).run()


if __name__ == "__main__":
    main()
