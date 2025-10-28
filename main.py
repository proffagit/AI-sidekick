from openai import OpenAI
import os
import json
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

with open('conf.json') as f:
    conf = json.load(f)

def query_llm(prompt, history=None, context=None, system_prompt=None, base="qwen", temperature=0.7, max_tokens=32768, baseurl=None, enable_thinking=True):
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
                temperature=0.6,
                top_p=0.95,
                max_tokens=max_tokens,
                stream=True  # Stream the response
            )
        else:
            response = client.chat.completions.create(
                model="my-model",
                messages=messages,
                temperature=0.7,
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
    system_prompt = "You are a helpful AI assistant. Be concise and clear in your responses."
    context = ""
    history = []
    enable_thinking = True  # Default thinking mode
    
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
        
        # Update history with the current exchange
        if isinstance(response, dict):
            history.extend([
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": response["full_response"]}
            ])
        else:
            # Handle case where response is a string (error message)
            history.extend([
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": response}
            ])