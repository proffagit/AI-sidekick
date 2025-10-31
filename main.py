from openai import OpenAI
import os
import json
import tiktoken
import signal
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
import requests
import re
import ast
from datetime import datetime
import threading
import time
from contextlib import contextmanager

# Global spinner state (supports nested usage across functions)
_spinner_lock = threading.Lock()
_spinner_counter = 0
_spinner_stop_event = None
_spinner_thread = None

def _spinner_worker(stop_event):
    frames = ["-", "/", "|", "\\"]
    i = 0
    local_console = Console()
    while not stop_event.is_set():
        frame = frames[i % len(frames)]
        local_console.print(f"[dim]{frame}[/dim]", end="\r", highlight=False)
        i += 1
        time.sleep(0.08)

def start_processing_spinner():
    """Start a subtle, global inline spinner. Returns a stop() callable.

    Safe for nested calls; only the outermost start actually spawns the thread.
    """
    global _spinner_counter, _spinner_thread, _spinner_stop_event
    with _spinner_lock:
        if _spinner_counter == 0:
            _spinner_stop_event = threading.Event()
            _spinner_thread = threading.Thread(target=_spinner_worker, args=(_spinner_stop_event,), daemon=True)
            _spinner_thread.start()
        _spinner_counter += 1

    def _stop():
        global _spinner_counter, _spinner_stop_event
        with _spinner_lock:
            if _spinner_counter > 0:
                _spinner_counter -= 1
                if _spinner_counter == 0 and _spinner_stop_event is not None:
                    _spinner_stop_event.set()
                    try:
                        Console().print(" " * 10, end="\r")
                    except Exception:
                        pass
    return _stop

@contextmanager
def processing_spinner():
    """Context manager wrapper around start_processing_spinner()."""
    stop = start_processing_spinner()
    try:
        yield
    finally:
        stop()

with open('conf.json') as f:
    conf = json.load(f)

# Get chat sliding window size from config, default to 4000 tokens if not specified
CHAT_SLIDING_WINDOW_MAX_TOKENS = conf.get('chat_sliding_window_max_size', 4000)

# Prevent double-saving on autosave and Ctrl+C in quick succession
keyboard_interupt_double_autosave_prevention_bool = False

def count_tokens(messages):
    """Count the total number of tokens in a list of messages."""
    try:
        encoding = tiktoken.get_encoding("cl100k_base")  # This is used by gpt-3.5-turbo and gpt-4
        num_tokens = 0
        for message in messages:
            # Every message follows format: {"role": "user", "content": "..."} 
            # Add 4 tokens for message format overhead
            num_tokens += 4
            for key, value in message.items():
                num_tokens += len(encoding.encode(value))
        return num_tokens
    except Exception as e:
        print(f"Warning: Could not count tokens accurately: {str(e)}")
        # Fallback to rough character-based estimate
        return sum(len(str(m.get('content', ''))) // 4 for m in messages)


def generate_chat_tags(history, conf, system_prompt, current_user_input=None):
    """Generate detailed, lowercase tags from the chat transcript using the LLM."""
    # Build conversation text; include current user input when provided
    parts = [
        f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history
    ]
    if current_user_input:
        parts.append(f"user: {current_user_input}")
    conversation_text = "\n\n".join(parts)

    llm_base_url = conf['baseurl'][1]
    client = OpenAI(base_url=llm_base_url, api_key="dummy_api_key")

    system_msg = system_prompt or "You are a helpful assistant."
    prompt = (
        "You will receive a full chat transcript.\n"
        "Produce ONLY a Python list of detailed tags that uniquely identify this chat.\n"
        "Tags must be strings, lowercase, and specific.\n"
        "Output format strictly as:\n"
        "[\"tag1\", \"tag2\", ...]"
    )

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": prompt + "\n\nTranscript:\n" + conversation_text},
    ]

    with processing_spinner():
        resp = client.chat.completions.create(
            model="my-model",
            messages=messages,
            temperature=0.3,
            top_p=0.9,
            max_tokens=2048,
            stream=False,
        )

    content = resp.choices[0].message.content if resp and resp.choices else ""

    tags_list = []
    m_list = re.search(r"\[(?:.|\n)*?\]", content)
    if m_list:
        candidate = m_list.group(0)
        try:
            parsed = ast.literal_eval(candidate)
            if isinstance(parsed, list):
                tags_list = [str(t).strip().lower() for t in parsed if str(t).strip()]
        except Exception:
            pass
    if not tags_list:
        tags_list = [t.lower() for t in re.findall(r"['\"]([^'\"]+)['\"]", content)]

    # Normalize: lowercase already; replace spaces with '-', remove invalid chars, collapse '-' runs
    normalized = []
    seen = set()
    for raw in tags_list:
        t = str(raw).strip().lower()
        if not t or t == 'empty_transcript':
            continue
        t = re.sub(r"\s+", "-", t)
        t = re.sub(r"[^a-z0-9\-]", "-", t)
        t = re.sub(r"-+", "-", t).strip('-')
        if not t:
            continue
        if t in seen:
            continue
        seen.add(t)
        normalized.append(t)

    return normalized


def save_chat(history, conf, system_prompt):
    """Summarize chat via LLM and save to ws4sqlite server."""
    console = Console()
    try:
        conversation_text = "\n\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history
        )

        llm_base_url = conf['baseurl'][1]
        client = OpenAI(base_url=llm_base_url, api_key="dummy_api_key")

        system_msg = system_prompt or "You are a helpful assistant."
        prompt = (
            "You will receive a full chat transcript.\n"
            "1) Produce a concise, high-signal summary.\n"
            "2) Then produce a Python list of detailed tags that uniquely identify this chat.\n"
            "Tags must be strings, lowercase, and specific.\n"
            "Output format strictly as:\n"
            "Summary: <one-line or short paragraph>\n"
            "Tags: [\"tag1\", \"tag2\", ...]"
        )

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt + "\n\nTranscript:\n" + conversation_text},
        ]

        with processing_spinner():
            resp = client.chat.completions.create(
                model="my-model",
                messages=messages,
                temperature=0.5,
                top_p=0.9,
                max_tokens=8192,
                stream=False,
            )

        content = resp.choices[0].message.content if resp and resp.choices else ""

        m_sum = re.search(r"Summary:\s*(.+)", content, re.IGNORECASE | re.DOTALL)
        summary = ""
        if m_sum:
            summary = m_sum.group(1).strip()
            summary = re.split(r"\n\s*Tags:\s*\[", summary)[0].strip()
        if not summary:
            summary = content.strip()[:1000]

        # Use shared tag generator for consistency
        tags_list = generate_chat_tags(history, conf, system_prompt)
        tags_field = " ".join(sorted(set(tags_list)))[:1024]

        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        server_url = conf.get('chat_history_server_url') or "http://127.0.0.1:12321/chat_history"
        auth_user = conf.get('chat_history_server_auth_user') or "admin"
        auth_pass = conf.get('chat_history_server_auth_pass') or "YourSuperSecretPass123"

        payload = {
            "transaction": [
                {
                    "statement": "INSERT INTO chat_history (summary, tags, date, time) VALUES (:summary, :tags, :date, :time)",
                    "values": {
                        "summary": summary,
                        "tags": tags_field,
                        "date": date_str,
                        "time": time_str,
                    },
                }
            ]
        }

        with processing_spinner():
            r = requests.post(
                server_url,
                json=payload,
                auth=(auth_user, auth_pass),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
        r.raise_for_status()

        console.print()
        console.print(Panel(
            Text("Chat autosave complete ✓", style="bold green"),
            title="Autosave",
            border_style="green"
        ))
    except Exception as e:
        console.print(Panel(
            Text(f"Chat autosave failed: {str(e)}", style="bold red"),
            title="Autosave Error",
            border_style="red"
        ))

def find_chat_summaries(history, conf, system_prompt, current_user_input=None):
    """Generate tags from current chat (same as save_chat) and fetch summaries.

    Returns a list of {"summary": str, "date": str} limited by conf['max_chat_history_results'].
    """
    console = Console()
    try:
        # Generate tags via shared function
        tags_list = generate_chat_tags(history, conf, system_prompt, current_user_input=current_user_input)

        # If no tags could be parsed, nothing to search
        if not tags_list:
            return []

        # Print tags inline using Rich
        try:
            from rich.text import Text
            tag_texts = []
            for t in tags_list:
                tag_texts.append(Text(t, style="bold magenta"))
            inline = Text("\nTags: ", style="cyan")
            for i, tt in enumerate(tag_texts):
                if i > 0:
                    inline.append(", ", style="dim")
                inline.append(tt)
            console.print(inline)
        except Exception:
            # Fallback plain print if Rich formatting fails for any reason
            print("Tags: " + ", ".join(tags_list))

        # Build SQL query using OR over all tags with fuzzy, case-insensitive matching
        where_clauses = []
        values = {}
        for idx, tag in enumerate(tags_list):
            key = f"t{idx}"
            where_clauses.append(f"LOWER(tags) LIKE :{key}")
            values[key] = f"%{tag}%"

        max_results = conf.get('max_chat_history_results', 100)
        # Some drivers allow binding LIMIT; ws4sqlite supports bindings, use :limit
        values["limit"] = int(max_results)

        where_sql = " OR ".join(where_clauses) if where_clauses else "1=0"
        sql = (
            "SELECT summary, date FROM chat_history "
            f"WHERE ({where_sql}) "
            "ORDER BY id DESC "
            "LIMIT :limit"
        )

        server_url = conf.get('chat_history_server_url') or "http://127.0.0.1:12321/chat_history"
        auth_user = conf.get('chat_history_server_auth_user') or "admin"
        auth_pass = conf.get('chat_history_server_auth_pass') or "YourSuperSecretPass123"

        payload = {
            "transaction": [
                {
                    "query": sql,
                    "values": values,
                }
            ]
        }

        with processing_spinner():
            r = requests.post(
                server_url,
                json=payload,
                auth=(auth_user, auth_pass),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
        r.raise_for_status()

        data = r.json()

        # Prefer ws4sqlite standard shape: list of result objects with columns/rows
        results_list = []
        if isinstance(data, dict) and 'results' in data:
            data_iter = data.get('results')
        else:
            data_iter = data if isinstance(data, list) else [data]

        for item in data_iter:
            if isinstance(item, dict) and 'columns' in item and 'rows' in item:
                columns = item.get('columns') or []
                rows = item.get('rows') or []
                try:
                    idx_summary = columns.index('summary') if 'summary' in columns else None
                    idx_date = columns.index('date') if 'date' in columns else None
                except ValueError:
                    idx_summary = None
                    idx_date = None
                if idx_summary is not None:
                    for row in rows:
                        summary_val = row[idx_summary] if idx_summary < len(row) else None
                        date_val = row[idx_date] if (idx_date is not None and idx_date < len(row)) else None
                        if isinstance(summary_val, str):
                            results_list.append({
                                "summary": summary_val,
                                "date": str(date_val) if date_val is not None else ""
                            })

        # Fallback: recursively collect any dicts having summary/date
        if not results_list:
            def _collect_pairs(obj):
                pairs = []
                if isinstance(obj, dict):
                    if 'summary' in obj and isinstance(obj.get('summary'), str):
                        pairs.append({
                            "summary": obj.get('summary'),
                            "date": str(obj.get('date') or "")
                        })
                    for v in obj.values():
                        pairs.extend(_collect_pairs(v))
                elif isinstance(obj, list):
                    for it in obj:
                        pairs.extend(_collect_pairs(it))
                return pairs
            results_list = _collect_pairs(data)

        return results_list[: int(max_results)]
    except Exception as e:
        # On failure, return empty list
        try:
            console.print(Panel(
                Text(f"Find chat summaries failed: {str(e)}", style="bold red"),
                title="Find Summaries Error",
                border_style="red"
            ))
        except Exception:
            pass
        return []

def query_llm(prompt, history=None, context=None, system_prompt=None, base="qwen", temperature=0.99, max_tokens=32768, baseurl=None, enable_thinking=True):
    # Use provided baseurl or default to baseurl0
    base_url = baseurl 
    client = OpenAI(
        base_url=base_url,  # Using configured server
        api_key="dummy_api_key"  # Using dummy API key for local server
    )
    print(f"Sending request to: {base_url}")
    try:
        # Build messages array
        messages = []
        
        # Add system prompt if provided
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
            
        # Add context if provided
        if context:
            messages.append({"role": "system", "content": f"Context: {context}"})
            
        # Add conversation history if provided
        if history:
            messages.extend(history)
            
        # Add current prompt with Qwen's thinking flag
        if enable_thinking:
                # Qwen3's native thinking format
            messages.append({"role": "system", "content": "Please provide your reasoning in <think> tags before your answer."})
        messages.append({"role": "user", "content": prompt.strip()})
        
        # For Qwen, we'll use a single response with its built-in thinking format
        # Set recommended parameters for thinking mode
        # Start spinner before sending request; stop it on first streamed token
        stop_spinner = start_processing_spinner()

        if enable_thinking:
            response = client.chat.completions.create(
                model="my-model",
                messages=messages,
                temperature=0.99,
                top_p=0.95,
                max_tokens=max_tokens,
                stream=True  # Stream the response
            )
        else:
            response = client.chat.completions.create(
                model="my-model",
                messages=messages,
                temperature=0.99,
                top_p=0.8,
                max_tokens=max_tokens,
                stream=True  # Stream the response
            )

        
        # Stream and collect content
        current_content = ""
        print("\n", end="", flush=True)
        
        # Initialize Rich console
        console = Console()
        
        # Track if we're in the thinking or answer section
        in_thinking = False
        thinking_content = ""
        answer_content = ""
        
        # Live content for streaming display
        live_content = ""
        
        for chunk in response:
            try:
                if hasattr(chunk, 'choices') and chunk.choices:
                    delta = chunk.choices[0].delta
                    # Stop spinner on first token (either reasoning or content)
                    if stop_spinner is not None:
                        try:
                            stop_spinner()
                        except Exception:
                            pass
                        finally:
                            stop_spinner = None
                        console.print(" " * 10, end="\r")
                    if hasattr(delta, 'reasoning_content') and delta.reasoning_content is not None:
                        # This is the thinking part
                        if not in_thinking:
                            current_content += "<think>\n"
                            console.print("\n", end="")
                            in_thinking = True
                        content = delta.reasoning_content
                        thinking_content += content
                        # Print thinking in dim style
                        console.print(content, style="dim", end="", highlight=False)
                        current_content += content
                    elif hasattr(delta, 'content') and delta.content is not None:
                        # This is the answer part
                        if in_thinking:
                            current_content += "</think>\n\n"
                            console.print("\n", end="")
                            in_thinking = False
                            # Display collected thinking in a panel
                            console.print(Panel(
                                Text(thinking_content.strip(), style="dim"),
                                title="Thinking Process",
                                border_style="dim"
                            ))
                            console.print()  # Add spacing
                        content = delta.content
                        answer_content += content
                        # Collect content for live display
                        live_content += content
                        # Print without markdown for streaming
                        console.print(content, end="", highlight=False)
                        current_content += content
                    elif delta.role == 'assistant':
                        # Skip initial role marker
                        continue
            except Exception as e:
                console.print(f"[red]Error: {str(e)}[/red]", flush=True)
        
        # Ensure spinner is stopped if still running
        if stop_spinner is not None:
            try:
                stop_spinner()
            except Exception:
                pass
            console.print(" " * 10, end="\r")

        # Close thinking tags and display final panel if we're still in thinking mode
        if in_thinking:
            current_content += "</think>\n"
            console.print("\n", end="")
            console.print(Panel(
                Text(thinking_content.strip(), style="dim"),
                title="Thinking Process",
                border_style="dim"
            ))
            console.print()  # Add spacing
        
        # Add a separator line between streaming and final rendering
        console.print("\n")
        console.print("─" * console.width, style="dim")
        console.print("\n[bold blue]Final Formatted Output:[/bold blue]\n")
        
        # Render the final answer with proper markdown formatting
        if answer_content.strip():
            console.print(Markdown(answer_content.strip()))
        
        print("\n")  # New line after response
        
        # Process the complete content
        full_content = current_content
        
        # Extract thinking and answer using Qwen's <think> tag format
        think_match = ""
        answer = ""
        
        if "<think>" in full_content and "</think>" in full_content:
            try:
                # Split at </think> tag
                parts = full_content.split("</think>", 1)
                # Extract content between <think> tags
                think_match = parts[0].split("<think>")[1].strip()
                # Everything after </think> is the answer
                answer = parts[1].strip() if len(parts) > 1 else ""
            except IndexError:
                # If splitting fails, treat everything as answer
                answer = full_content.strip()
        else:
            # No think tags found, entire content is the answer
            answer = full_content.strip()
        
        return {
            "thinking": think_match,
            "answer": answer,
            "full_response": full_content.strip()
        }
    except Exception as e:
        # Stop spinner on error
        try:
            stop_spinner()
        except Exception:
            pass
        error_msg = f"Error: {str(e)}"
        print(f"Debug: Exception occurred - {error_msg}", flush=True)
        return {
            "thinking": "",
            "answer": error_msg,
            "full_response": error_msg
        }

if __name__ == "__main__":
    # Initialize conversation context, history, and system prompt
    try:
        with open('ai_personality_profile.txt', 'r') as f:
            personality_profile = f.read()
            system_prompt = personality_profile
    except FileNotFoundError:
        print("Note: AI personality profile not found, using default system prompt")
        system_prompt = "You are a helpful AI assistant. Be concise and clear in your responses."
    except Exception as e:
        print(f"Warning: Could not read personality profile: {str(e)}")
        system_prompt = "You are a helpful AI assistant. Be concise and clear in your responses."

    context = ""
    history = []
    enable_thinking = True  # Default thinking mode
    total_tokens_used = 0  # Reset token counter at the start of each session
    
    console = Console()
    console.print("[bold blue]Welcome to AI Sidekick![/bold blue] Type [yellow]'quit'[/yellow] to exit.")
    
    # Define and register Ctrl+C handler to autosave chat with required args
    def _handle_sigint(signum, frame):
        try:
            global keyboard_interupt_double_autosave_prevention_bool
            if not keyboard_interupt_double_autosave_prevention_bool:
                save_chat(history, conf, system_prompt)
        finally:
            console = Console()
            console.print("\n[dim]Session terminated.[/dim]")
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handle_sigint)
    
    while True:
        # Get user input
        console.print("\n[bold green]You:[/bold green] ", end="")
        user_input = input().strip()
        
        # Check for exit condition
        if user_input.lower() in ['quit', 'exit']:
            print("Goodbye!")
            break

        chat_history_summaries = find_chat_summaries(history, conf, system_prompt, current_user_input=user_input)
        # Append previous chat summaries (with dates) to the existing context for the LLM
        if chat_history_summaries:
            summaries_lines = [
                "Use the following concise summaries of previous chats as supplemental context. Guidelines for using them:",
                "- Treat them as high-level reminders; do not assume unstated facts.",
                "- Prefer more recent items when resolving conflicts.",
                "- If you draw from a summary, reference its date.",
                "- Do not invent details not present in the summaries.",
                "- Ignore items irrelevant to the current request.",
                "- If a summary conflicts with the user's current instructions, follow the current instructions.",
                "",
                "Previous chat summaries:"]
            for item in chat_history_summaries:
                date_str = item.get("date", "") or ""
                summary_str = item.get("summary", "") or ""
                if date_str:
                    summaries_lines.append(f"- [{date_str}] {summary_str}")
                else:
                    summaries_lines.append(f"- {summary_str}")
            summaries_block = "\n".join(summaries_lines)
            if context:
                context = f"{context}\n\n{summaries_block}"
            else:
                context = summaries_block

        

        # Query LLM with context and history
        response = query_llm(
            prompt=user_input,
            history=history,
            context=context,
            system_prompt=system_prompt,
            baseurl=conf['baseurl'][0]  # Using first base URL from config
        )
        
        # No need to print response here as it's already streamed
        
        # Update history with the current exchange and maintain token-based sliding window
        new_messages = [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": response["full_response"] if isinstance(response, dict) else response}
        ]
        
        # Count tokens in new messages and update total for this session
        new_tokens = count_tokens(new_messages)
        total_tokens_used += new_tokens
        console.print(f"[dim]Total tokens used in this session: {total_tokens_used}[/dim]\n")
        
        # Add new messages
        history.extend(new_messages)
        # Reset prevention flag on new user input/change in history
        keyboard_interupt_double_autosave_prevention_bool = False
        
        # Trigger save when total tokens cross a multiple of the sliding window size
        window = CHAT_SLIDING_WINDOW_MAX_TOKENS
        if window and total_tokens_used > 0:
            prev_total = total_tokens_used - new_tokens
            if (prev_total // window) < (total_tokens_used // window):
                save_chat(history, conf, system_prompt)
                # Set prevention flag so immediate Ctrl+C won't double-save
                keyboard_interupt_double_autosave_prevention_bool = True
        
        # Check token count and trim history if needed
        while history and count_tokens(history) > CHAT_SLIDING_WINDOW_MAX_TOKENS:
            # Remove oldest message pair (user + assistant messages)
            if len(history) >= 2:
                history = history[2:]
            else:
                # If somehow we have an odd number of messages, just remove the oldest
                history = history[1:]