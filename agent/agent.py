import anthropic
import json
import logging
from collections.abc import Mapping
from django.conf import settings
from django.utils import timezone
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "list_files",
        "description": (
            "List files and directories in the repository. Start with subpath='' for top-level. "
            "Navigate deeper with specific subpaths like 'src' or 'fastapi/dependencies'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subpath": {
                    "type": "string",
                    "description": "Subdirectory to list. Empty string for root.",
                    "default": "",
                }
            },
            "required": [],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a specific file's content with line numbers. "
            "Only use this when you've identified the file is directly relevant."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to the file from repo root, e.g. 'fastapi/routing.py'",
                }
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "search_code",
        "description": (
            "Search for a term or pattern across all code files. "
            "Best for finding where something is defined or used."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term or pattern to find in the codebase",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_file_summary",
        "description": (
            "Get a quick overview of a file (first 30 + last 10 lines) without reading everything. "
            "Use before read_file to decide if a file is worth reading fully."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to file from repo root",
                }
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "save_finding",
        "description": (
            "Save an important discovery to the database. "
            "Call this whenever you find something directly relevant to the question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "File where this finding was discovered",
                },
                "note": {
                    "type": "string",
                    "description": "What you found and why it matters for the question",
                },
                "line_reference": {
                    "type": "string",
                    "description": "Line reference, e.g. 'L42-L78' or 'L120'",
                    "default": "",
                },
            },
            "required": ["file_path", "note"],
        },
    },
    {
        "name": "get_previous_findings",
        "description": (
            "Check what was already discovered in previous research sessions for this repo. "
            "ALWAYS call this first before exploring."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_past_sessions",
        "description": "See what questions have been asked about this repo before.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "finish",
        "description": (
            "Call this when you have enough information to give a complete, well-referenced answer. "
            "This ends the research session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": (
                        "Complete, detailed answer to the user's question "
                        "with specific file and line references"
                    ),
                },
                "file_references": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of key files examined, e.g. ['fastapi/routing.py']",
                },
            },
            "required": ["answer", "file_references"],
        },
    },
]

SYSTEM_PROMPT = """You are a codebase research agent. Your job is to answer technical \
questions about a GitHub repository by exploring its code using the tools provided.

WORKFLOW (follow this order):
1. ALWAYS start with get_previous_findings() — check what's already known
2. Use list_files() with empty subpath to understand the top-level structure
3. Use search_code() to quickly locate relevant code by keyword
4. Use get_file_summary() on promising files before reading them fully
5. Use read_file() only on files directly relevant to the question
6. Use save_finding() whenever you discover something important
7. Call finish() once you have enough for a confident, referenced answer

SMART NAVIGATION RULES:
- Do NOT read every file — be selective based on the question
- Do NOT call list_files() on the entire repo — go deeper only where needed
- Use search_code() to jump directly to relevant code
- 3 focused tool calls is better than 15 unfocused ones
- You have a maximum of {max_steps} tool calls — use them wisely

ANSWER QUALITY:
- Always reference specific files and line numbers
- Explain the mechanism, not just where it is
- Be technically accurate and concise
- If previous findings already answer the question, say so and build on them

COST AWARENESS:
- Prefer search_code() over reading entire files
- Use get_file_summary() before read_file()
- Stop exploring as soon as you have enough information
"""


def _build_gemini_tool() -> types.Tool:
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters_json_schema=t["input_schema"],
            )
            for t in TOOL_DEFINITIONS
        ],
    )


class CodebaseResearchAgent:
    def __init__(self, session, repo_local_path: str):
        self.session = session
        self.repo_local_path = repo_local_path
        self.repo_url = session.repo.url
        provider = getattr(settings, "LLM_PROVIDER", "anthropic")
        if provider == "gemini":
            if not getattr(settings, "GEMINI_API_KEY", None):
                raise RuntimeError(
                    "GEMINI_API_KEY, GOOGLE_API_KEY, or Gemini_API_Key must be set for Gemini provider."
                )
            self._genai_client = genai.Client(api_key=settings.GEMINI_API_KEY)
            self._gemini_tool = _build_gemini_tool()
            self.client = None
        else:
            if not getattr(settings, "ANTHROPIC_API_KEY", None):
                raise RuntimeError("ANTHROPIC_API_KEY must be set for Anthropic provider.")
            self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            self._genai_client = None
            self._gemini_tool = None
        self.messages = []
        self.step_count = 0
        self.max_steps = settings.MAX_AGENT_STEPS
        self.total_tokens = 0

    def _build_system_prompt(self) -> str:
        return SYSTEM_PROMPT.format(max_steps=self.max_steps)

    @staticmethod
    def _normalize_function_call_args(fc) -> dict:
        raw = getattr(fc, "args", None)
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return dict(raw)
        if hasattr(raw, "model_dump"):
            dumped = raw.model_dump()
            return dumped if isinstance(dumped, dict) else {}
        if isinstance(raw, Mapping):
            return dict(raw.items())
        return {}

    def _accumulate_gemini_usage(self, response):
        md = getattr(response, "usage_metadata", None)
        if md is None:
            return
        prompt = getattr(md, "prompt_token_count", None) or 0
        candidates = getattr(md, "candidates_token_count", None) or 0
        self.total_tokens += int(prompt) + int(candidates)

    @staticmethod
    def _gemini_response_text(response) -> str:
        text = getattr(response, "text", None)
        if text and text.strip():
            return text.strip()
        for cand in getattr(response, "candidates", None) or []:
            content = getattr(cand, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", None) or []:
                pt = getattr(part, "text", None)
                if pt and pt.strip():
                    return pt.strip()
        return ""

    def _gemini_generate(self, contents, *, tools_enabled: bool):
        cfg_kw = {
            "system_instruction": self._build_system_prompt(),
            "max_output_tokens": 2048,
            "temperature": 0.2,
        }
        if tools_enabled:
            cfg_kw["tools"] = [self._gemini_tool]
            cfg_kw["max_output_tokens"] = 4096
        cfg = types.GenerateContentConfig(**cfg_kw)
        return self._genai_client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=contents,
            config=cfg,
        )

    def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        from agent.tools.code_tools import (
            list_files, read_file, search_code, get_file_summary,
        )
        from agent.tools.db_tools import (
            save_finding, get_previous_findings, list_past_sessions, log_tool_call,
        )

        try:
            if tool_name == "list_files":
                result = list_files(self.repo_local_path, tool_input.get("subpath", ""))
            elif tool_name == "read_file":
                result = read_file(self.repo_local_path, tool_input["file_path"])
            elif tool_name == "search_code":
                result = search_code(self.repo_local_path, tool_input["query"])
            elif tool_name == "get_file_summary":
                result = get_file_summary(self.repo_local_path, tool_input["file_path"])
            elif tool_name == "save_finding":
                result = save_finding(
                    session_id=self.session.id,
                    file_path=tool_input["file_path"],
                    note=tool_input["note"],
                    line_reference=tool_input.get("line_reference", ""),
                )
            elif tool_name == "get_previous_findings":
                result = get_previous_findings(self.repo_url)
            elif tool_name == "list_past_sessions":
                result = list_past_sessions(self.repo_url)
            else:
                result = {"error": f"Unknown tool: {tool_name}"}

            success = "error" not in result
            output_str = json.dumps(result)

        except Exception as e:
            result = {"error": str(e)}
            output_str = json.dumps(result)
            success = False
            logger.error(f"Tool {tool_name} failed: {e}")

        log_tool_call(
            session_id=self.session.id,
            tool_name=tool_name,
            input_args=tool_input,
            output_summary=output_str[:1000],
            success=success,
        )

        self.step_count += 1
        return output_str

    def _run_anthropic(self) -> str:
        self.messages = [{"role": "user", "content": self.session.question}]
        answer = ""

        while True:
            # Force-stop: exceeded max steps
            if self.step_count >= self.max_steps:
                logger.warning(f"Session {self.session.id}: max steps reached, forcing finish")
                self.messages.append({
                    "role": "user",
                    "content": (
                        "You have reached the maximum number of tool calls. "
                        "Please provide your best answer now based on what you have found so far."
                    ),
                })
                final_response = self.client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=2048,
                    system=self._build_system_prompt(),
                    messages=self.messages,
                )
                self.total_tokens += (
                    final_response.usage.input_tokens + final_response.usage.output_tokens
                )
                for block in final_response.content:
                    if hasattr(block, "text"):
                        answer = block.text
                break

            # Token budget exceeded
            if self.total_tokens >= settings.MAX_TOKENS_BUDGET:
                logger.warning(f"Session {self.session.id}: token budget exhausted")
                answer = (
                    "Research stopped: token budget exceeded. "
                    "Please check the findings saved so far for partial results."
                )
                break

            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=self._build_system_prompt(),
                tools=TOOL_DEFINITIONS,
                messages=self.messages,
            )

            self.total_tokens += response.usage.input_tokens + response.usage.output_tokens

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        answer = block.text
                break

            if response.stop_reason == "tool_use":
                self.messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                finish_answer = None

                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    tool_name = block.name
                    tool_input = block.input

                    if tool_name == "finish":
                        finish_answer = tool_input.get("answer", "")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps({"status": "session_finished"}),
                        })
                        self.step_count += 1
                        from agent.tools.db_tools import log_tool_call

                        log_tool_call(
                            session_id=self.session.id,
                            tool_name="finish",
                            input_args=tool_input,
                            output_summary="Session finished by agent",
                            success=True,
                        )
                    else:
                        result_str = self._execute_tool(tool_name, tool_input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_str,
                        })

                self.messages.append({"role": "user", "content": tool_results})

                if finish_answer is not None:
                    answer = finish_answer
                    break
            else:
                logger.warning(f"Unexpected stop_reason: {response.stop_reason}")
                break

        return answer

    def _run_gemini(self) -> str:
        from agent.tools.db_tools import log_tool_call

        contents: list = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=self.session.question)],
            ),
        ]
        answer = ""

        blocking_reasons = frozenset(
            {
                types.FinishReason.SAFETY,
                types.FinishReason.BLOCKLIST,
                types.FinishReason.PROHIBITED_CONTENT,
                types.FinishReason.SPII,
                types.FinishReason.RECITATION,
            },
        )

        while True:
            if self.step_count >= self.max_steps:
                logger.warning(f"Session {self.session.id}: max steps reached, forcing finish")
                contents.append(types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(
                            text=(
                                "You have reached the maximum number of tool calls. "
                                "Please provide your best answer now based on what you have found."
                            ),
                        ),
                    ],
                ))
                resp = self._gemini_generate(contents, tools_enabled=False)
                self._accumulate_gemini_usage(resp)
                answer = self._gemini_response_text(resp)
                break

            if self.total_tokens >= settings.MAX_TOKENS_BUDGET:
                logger.warning(f"Session {self.session.id}: token budget exhausted")
                answer = (
                    "Research stopped: token budget exceeded. "
                    "Please check the findings saved so far for partial results."
                )
                break

            response = self._gemini_generate(contents, tools_enabled=True)
            self._accumulate_gemini_usage(response)

            if not getattr(response, "candidates", None):
                fb = getattr(response, "prompt_feedback", None)
                msg = getattr(fb, "block_reason", None) if fb else None
                raise RuntimeError(msg or "Gemini returned no response candidates.")

            cand = response.candidates[0]
            if cand.finish_reason in blocking_reasons and not (response.function_calls or []):
                raise RuntimeError(
                    f"Gemini stopped with finish_reason={cand.finish_reason!r}; no tool calls."
                )

            fc_list = list(getattr(response, "function_calls", None) or [])

            if not fc_list:
                answer = self._gemini_response_text(response)
                if not answer:
                    raise RuntimeError(
                        f"Gemini returned empty text and no tool calls "
                        f"(finish_reason={cand.finish_reason!r})."
                    )
                break

            contents.append(response.candidates[0].content)

            finish_answer = None
            tool_parts: list = []

            for fc in fc_list:
                tool_name = fc.name
                tool_input = self._normalize_function_call_args(fc)

                if tool_name == "finish":
                    finish_answer = tool_input.get("answer", "")
                    tool_parts.append(
                        types.Part.from_function_response(
                            name=tool_name,
                            response={"result": '{"status":"session_finished"}'},
                        ),
                    )
                    self.step_count += 1
                    log_tool_call(
                        session_id=self.session.id,
                        tool_name="finish",
                        input_args=tool_input,
                        output_summary="Session finished by agent",
                        success=True,
                    )
                else:
                    result_str = self._execute_tool(tool_name, tool_input)
                    tool_parts.append(
                        types.Part.from_function_response(
                            name=tool_name,
                            response={"result": result_str},
                        ),
                    )

            contents.append(types.Content(role="tool", parts=tool_parts))

            if finish_answer is not None:
                answer = finish_answer
                break

        return answer

    def run(self) -> str:
        try:
            if getattr(settings, "LLM_PROVIDER", "anthropic") == "gemini":
                answer = self._run_gemini()
            else:
                answer = self._run_anthropic()

            self.session.final_answer = answer
            self.session.status = "completed"
            self.session.completed_at = timezone.now()
            self.session.total_tokens_used = self.total_tokens
            self.session.save()
            return answer

        except Exception as e:
            logger.error(f"Agent run failed for session {self.session.id}: {e}", exc_info=True)
            self.session.status = "failed"
            self.session.error_message = str(e)
            self.session.completed_at = timezone.now()
            self.session.save()
            raise
