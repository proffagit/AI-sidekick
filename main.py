from openai import OpenAI
import os
import json

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
        
        # ANSI color codes
        GRAY = "\033[90m"  # Gray for thinking
        WHITE = "\033[37m"  # White for answer
        RESET = "\033[0m"  # Reset color
        
        # Track if we're in the thinking or answer section
        in_thinking = False
        
        for chunk in response:
            try:
                if hasattr(chunk, 'choices') and chunk.choices:
                    delta = chunk.choices[0].delta
                    if hasattr(delta, 'reasoning_content') and delta.reasoning_content is not None:
                        # This is the thinking part
                        if not in_thinking:
                            current_content += "<think>\n"
                            print(f"\n{GRAY}<think>", flush=True)
                            in_thinking = True
                        content = delta.reasoning_content
                        print(f"{GRAY}{content}{RESET}", end="", flush=True)
                        current_content += content
                    elif hasattr(delta, 'content') and delta.content is not None:
                        # This is the answer part
                        if in_thinking:
                            current_content += "</think>\n\n"
                            print(f"{GRAY}</think>{RESET}\n", flush=True)
                            in_thinking = False
                        content = delta.content
                        print(f"{WHITE}{content}{RESET}", end="", flush=True)
                        current_content += content
                    elif delta.role == 'assistant':
                        # Skip initial role marker
                        continue
            except Exception as e:
                print(f"Debug: Error processing chunk: {str(e)}", flush=True)
        
        # Close thinking tags if we're still in thinking mode
        if in_thinking:
            current_content += "</think>\n"
            print(f"{GRAY}</think>{RESET}", flush=True)
        
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
    
    print("Welcome to AI Sidekick! Type 'quit' to exit.")
    
    while True:
        # Get user input
        user_input = input("\nYou: ").strip()
        
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