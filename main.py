from openai import OpenAI
import os
import json
import tiktoken
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

with open('conf.json') as f:
    conf = json.load(f)

# Get chat sliding window size from config, default to 4000 tokens if not specified
CHAT_SLIDING_WINDOW_MAX_TOKENS = conf.get('chat_sliding_window_max_size', 4000)

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
    
    while True:
        # Get user input
        console.print("\n[bold green]You:[/bold green] ", end="")
        user_input = input().strip()
        
        # Check for exit condition
        if user_input.lower() in ['quit', 'exit']:
            print("Goodbye!")
            break
        
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
        
        # Check token count and trim history if needed
        while history and count_tokens(history) > CHAT_SLIDING_WINDOW_MAX_TOKENS:
            # Remove oldest message pair (user + assistant messages)
            if len(history) >= 2:
                history = history[2:]
            else:
                # If somehow we have an odd number of messages, just remove the oldest
                history = history[1:]