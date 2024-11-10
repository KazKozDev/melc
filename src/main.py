import asyncio
import aiohttp
import json
import re
from typing import List, Dict, Any

class LLMModel:
    """
    Class for interacting with an LLM model via API.
    """
    def __init__(self, name: str, base_url: str = "http://localhost:11434/api"):
        self.name = name
        self.base_url = base_url

    async def generate(self, prompt: str, max_tokens: int = 4096) -> str:
        """
        Sends a prompt to the model and retrieves the generated response.

        Args:
            prompt (str): The prompt to send to the model.
            max_tokens (int): The maximum number of tokens to generate.

        Returns:
            str: The generated response or an error message.
        """
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{self.base_url}/generate",
                    json={
                        "model": self.name,
                        "prompt": prompt,
                        "max_tokens": max_tokens
                    },
                    headers={'Accept': 'application/json'},
                    timeout=aiohttp.ClientTimeout(total=180)
                ) as response:
                    response.raise_for_status()

                    if response.content_type == 'application/x-ndjson':
                        result = ""
                        async for line in response.content:
                            data = json.loads(line)
                            if 'response' in data:
                                result += data['response']
                    else:
                        data = await response.json()
                        result = data.get('response', '')

                    return result.strip()
            except aiohttp.ClientError:
                return f"Error: Unable to generate response for {self.name}."
            except asyncio.TimeoutError:
                return f"Error: Request to {self.name} timed out."
            except Exception:
                return f"Error: Unable to generate response for {self.name}."

class InteractionSystem:
    """
    Main class for the user interaction system.
    All models participate in the initial phase, then the main model critiques and generates the final answer.
    """
    def __init__(self, experts: Dict[str, LLMModel], critic_model_name: str):
        self.experts = experts
        self.critic_model = experts.get(critic_model_name)
        if not self.critic_model:
            raise ValueError(f"Critic model '{critic_model_name}' not found among experts.")
        self.conversation_history: List[str] = []

    async def interact(self, max_iterations: int = 5):
        """
        Handles the interaction loop with the user.

        Args:
            max_iterations (int): The maximum number of interaction iterations.

        Returns:
            str: Completion message.
        """
        for i in range(max_iterations):
            print(f"\n--- Iteration {i + 1} ---")
            user_question = input("Enter your question (or press Enter to finish): ").strip()
            if not user_question:
                print("Interaction ended by the user.")
                break

            self.conversation_history.append(f"User: {user_question}")

            selected_experts = list(self.experts.values())

            # Initial response phase
            initial_tasks = [
                expert.generate(
                    f"Please answer the following question accurately and concisely. "
                    f"Also, indicate your confidence level from 1 to 5.\n"
                    f"Question: {user_question}\n"
                    f"Answer with confidence level:"
                ) for expert in selected_experts
            ]
            initial_responses = await asyncio.gather(*initial_tasks)

            expert_responses = {}
            for expert, response in zip(selected_experts, initial_responses):
                if "Error" in response:
                    answer = "Sorry, I couldn't generate an answer."
                    confidence = 1
                else:
                    confidence_match = re.search(r"Confidence[:\s]*(\d+)", response, re.IGNORECASE)
                    confidence = int(confidence_match.group(1)) if confidence_match else 3
                    answer = re.sub(r"Confidence[:\s]*\d+", "", response, flags=re.IGNORECASE).strip()
                expert_responses[expert.name] = {
                    'answer': answer,
                    'confidence': confidence
                }

            print("\n--- Initial Expert Responses ---")
            for name, data in expert_responses.items():
                print(f"{name} (Confidence {data['confidence']}): {data['answer']}")
                self.conversation_history.append(
                    f"{name} (Confidence {data['confidence']}): {data['answer']}"
                )

            # Critique phase by the main model
            print("\n--- Critique by the Main Model ---")
            critiques = []
            critique_tasks = []
            for name, data in expert_responses.items():
                if name == self.critic_model.name:
                    continue  # The main model does not critique itself
                critique_prompt = (
                    f"Question: {user_question}\n"
                    f"Expert {name}'s answer: {data['answer']}\n"
                    f"Task: Evaluate Expert {name}'s answer for inaccuracies, omissions, or possible improvements. "
                    f"Also, indicate your confidence level in the critique from 1 to 5.\n"
                    f"Critique with confidence level:"
                )
                critique_tasks.append(self.critic_model.generate(critique_prompt))
                critiques.append(name)
            
            if critique_tasks:
                critiques_results = await asyncio.gather(*critique_tasks)
            else:
                critiques_results = []

            critiques_about_experts = {}
            for critique_result, name in zip(critiques_results, critiques):
                if "Error" in critique_result:
                    critique_text = "Unable to provide critique."
                    confidence = 1
                else:
                    confidence_match = re.search(r"Confidence[:\s]*(\d+)", critique_result, re.IGNORECASE)
                    confidence = int(confidence_match.group(1)) if confidence_match else 3
                    critique_text = re.sub(r"Confidence[:\s]*\d+", "", critique_result, flags=re.IGNORECASE).strip()
                critiques_about_experts[name] = {
                    'critique': critique_text,
                    'confidence': confidence
                }
                print(
                    f"{self.critic_model.name} critiques {name} "
                    f"(Confidence {confidence}): {critique_text}"
                )
                self.conversation_history.append(
                    f"{self.critic_model.name} critiques {name} "
                    f"(Confidence {confidence}): {critique_text}"
                )

            # Final answer generation phase by the main model
            print("\n--- Generating Final Answer by the Main Model ---")
            combined_critiques = "\n".join([
                f"Critique for {name}: {data['critique']} (Confidence {data['confidence']})"
                for name, data in critiques_about_experts.items()
            ])
            final_answer_prompt = (
                f"Question: {user_question}\n"
                f"Initial expert responses:\n" +
                "\n".join([
                    f"{name} (Confidence {data['confidence']}): {data['answer']}"
                    for name, data in expert_responses.items()
                ]) +
                f"\nCritiques by {self.critic_model.name}:\n{combined_critiques}\n"
                f"Task: Based on the provided critiques, formulate the most accurate and comprehensive answer to the question. "
                f"Indicate your confidence level from 1 to 5.\n"
                f"Final answer with confidence level:"
            )

            final_response = await self.critic_model.generate(final_answer_prompt)

            if "Error" in final_response:
                final_answer = "Sorry, I couldn't generate the final answer."
                final_confidence = 1
            else:
                confidence_match = re.search(r"Confidence[:\s]*(\d+)", final_response, re.IGNORECASE)
                final_confidence = int(confidence_match.group(1)) if confidence_match else 3
                final_answer = re.sub(r"Confidence[:\s]*\d+", "", final_response, flags=re.IGNORECASE).strip()

            print("\n--- Final Answer ---")
            print(f"{self.critic_model.name} (Confidence {final_confidence}): {final_answer}")
            self.conversation_history.append(
                f"Final answer from {self.critic_model.name} (Confidence {final_confidence}): {final_answer}"
            )
        
        print("\n--- Interaction Completed ---")
        return "Interaction successfully completed."

async def main():
    """
    The main function to initialize experts and start the interaction system.
    """
    experts = {
        "gemma2:9b": LLMModel("gemma2:9b"),
        "hermes3:latest": LLMModel("hermes3:latest"),
        "aya-expanse:latest": LLMModel("aya-expanse:latest")  # Ensure 'aya:latest' is correctly named and accessible
    }
    
    # Specify the name of the model that will critique and generate the final answer
    critic_model_name = "gemma2:9b"
    
    # Verify that the critic model is among the experts
    if critic_model_name not in experts:
        print(f"Critic model '{critic_model_name}' not found among the selected experts.")
        return
    
    system = InteractionSystem(experts, critic_model_name)
    
    try:
        result = await system.interact()
        print(result)
    except Exception:
        print("An error occurred during the interaction process.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram interrupted by the user.")
