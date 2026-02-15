"""LLM client interface."""

from abc import ABC, abstractmethod
from ..domain.models import LLMResult, JudgeResult


class ILLMClient(ABC):
    """Interface for LLM API clients."""
    
    @abstractmethod
    async def generate(
        self, 
        system_prompt: str, 
        user_prompt: str, 
        max_output_tokens: int,
        model: str | None = None
    ) -> LLMResult:
        """Generate a response from the LLM.
        
        Args:
            system_prompt: System prompt/context
            user_prompt: User's input prompt
            max_output_tokens: Maximum tokens for output
            model: Optional model override (uses default if None)
            
        Returns:
            LLMResult with generated output
        """
        pass
    
    @abstractmethod
    async def judge(
        self, 
        judge_prompt: str, 
        max_output_tokens: int = 100,
        model: str | None = None
    ) -> JudgeResult:
        """Call LLM to judge an output.
        
        Args:
            judge_prompt: The prompt for judging
            max_output_tokens: Maximum tokens for judge output
            model: Optional model override (uses default if None)
            
        Returns:
            JudgeResult with verdict and reason
        """
        pass
