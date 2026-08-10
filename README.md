# editor-agent

An autonomous text correction agent, with access to local files and Google Drive.

## Current state

What exists today is the groundwork: a conversation loop with an LLM and tool
calling over local files.

- `read_file` — read a file
- `list_files` — list files and directories
- `edit_file` — replace text, or create the file if it does not exist

## Goal

- Autonomous text correction, without asking for confirmation at every step
- Google Drive access on top of the local filesystem

## Usage

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export DEEPSEEK_API_KEY=...
python main.py
```

`ctrl-d` to quit.
