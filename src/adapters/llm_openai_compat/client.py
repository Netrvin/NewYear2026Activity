"""OpenAI-compatible LLM client implementation."""

import json
import logging
import time
from typing import Optional

from openai import AsyncOpenAI

from ...ports.llm import ILLMClient
from ...domain.models import LLMResult, JudgeResult, GradeVerdict

logger = logging.getLogger(__name__)


class OpenAICompatibleClient(ILLMClient):
    """OpenAI-compatible LLM client."""
    
    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = "gpt-4o-mini",
        timeout: int = 30
    ):
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout
        )
        self.model = model
        self.timeout = timeout
    
    async def generate(
        self, 
        system_prompt: str, 
        user_prompt: str, 
        max_output_tokens: int,
        model: str | None = None
    ) -> LLMResult:
        """Generate a response from the LLM."""
        use_model = model or self.model
        start_time = time.time()
        
        try:
            response = await self.client.chat.completions.create(
                model=use_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=max_output_tokens,
                temperature=0.7
            )
            
            latency_ms = int((time.time() - start_time) * 1000)
            output = response.choices[0].message.content or ""
            tokens_used = response.usage.total_tokens if response.usage else None
            
            return LLMResult(
                output=output,
                model=use_model,
                tokens_used=tokens_used,
                latency_ms=latency_ms
            )
        
        except Exception as e:
            logger.exception(f"LLM generate error: {e}")
            latency_ms = int((time.time() - start_time) * 1000)
            return LLMResult(
                output="",
                model=use_model,
                latency_ms=latency_ms,
                error=str(e)
            )
    
    async def judge(
        self, 
        judge_prompt: str, 
        max_output_tokens: int = 100,
        model: str | None = None
    ) -> JudgeResult:
        """Call LLM to judge an output."""
        use_model = model or self.model
        start_time = time.time()
        
        try:
            response = await self.client.chat.completions.create(
                model=use_model,
                messages=[
                    {"role": "system", "content": "You are a judge that evaluates outputs. Respond ONLY with a JSON object containing 'verdict' (PASS, FAIL, or SENSITIVE) and 'reason' (brief explanation)."},
                    {"role": "user", "content": judge_prompt}
                ],
                max_tokens=max_output_tokens,
                temperature=0
            )
            
            raw_output = response.choices[0].message.content or ""
            
            # Parse JSON response
            try:
                # Try to extract JSON from the response
                json_str = raw_output
                if "```json" in json_str:
                    json_str = json_str.split("```json")[1].split("```")[0]
                elif "```" in json_str:
                    json_str = json_str.split("```")[1].split("```")[0]
                
                result = json.loads(json_str.strip())
                verdict_str = result.get('verdict', '').upper()
                reason = result.get('reason', 'No reason provided')
                
                if verdict_str == 'PASS':
                    verdict = GradeVerdict.PASS
                elif verdict_str == 'SENSITIVE':
                    verdict = GradeVerdict.SENSITIVE
                elif verdict_str == 'FAIL':
                    verdict = GradeVerdict.FAIL
                else:
                    # Unknown verdict value
                    verdict = GradeVerdict.FAIL
                    reason = f"Unknown verdict '{verdict_str}': {reason}"
                    return JudgeResult(
                        verdict=verdict,
                        reason=reason,
                        raw_output=raw_output,
                        error=f"parse_error: unknown verdict '{verdict_str}'"
                    )
                
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                # JSON parsing failed entirely
                verdict = GradeVerdict.FAIL
                reason = f"Could not parse JSON response: {raw_output[:100]}"
                return JudgeResult(
                    verdict=verdict,
                    reason=reason,
                    raw_output=raw_output,
                    error=f"parse_error: {e}"
                )
            
            return JudgeResult(
                verdict=verdict,
                reason=reason,
                raw_output=raw_output
            )
        
        except Exception as e:
            logger.exception(f"LLM judge error: {e}")
            return JudgeResult(
                verdict=GradeVerdict.FAIL,
                reason=f"Judge error: {str(e)}",
                raw_output="",
                error=str(e)
            )


class MockLLMClient(ILLMClient):
    """Mock LLM client for testing."""
    
    def __init__(self):
        self._generate_responses: dict = {}
        self._generate_errors: dict = {}
        self._judge_responses: dict = {}
        self._default_generate_output = "Default mock output"
        self._default_generate_error: Optional[str] = None
        self._default_judge_verdict = GradeVerdict.PASS
    
    def set_generate_response(self, user_prompt_contains: str, output: str):
        """Set a mock response for generate calls."""
        self._generate_responses[user_prompt_contains] = output

    def set_generate_error(self, user_prompt_contains: str, error_message: str):
        """Set a mock error for generate calls."""
        self._generate_errors[user_prompt_contains] = error_message
    
    def set_judge_response(self, prompt_contains: str, verdict: GradeVerdict, reason: str = "Mock reason"):
        """Set a mock response for judge calls."""
        self._judge_responses[prompt_contains] = (verdict, reason)
    
    def set_default_generate_output(self, output: str):
        """Set default output for generate calls."""
        self._default_generate_output = output

    def set_default_generate_error(self, error_message: Optional[str]):
        """Set default error for generate calls (None to disable)."""
        self._default_generate_error = error_message
    
    def set_default_judge_verdict(self, verdict: GradeVerdict):
        """Set default verdict for judge calls."""
        self._default_judge_verdict = verdict
    
    async def generate(
        self, 
        system_prompt: str, 
        user_prompt: str, 
        max_output_tokens: int,
        model: str | None = None
    ) -> LLMResult:
        """Generate a mock response."""
        use_model = model or "mock"
        for key, error_message in self._generate_errors.items():
            if key in user_prompt:
                return LLMResult(
                    output="",
                    model=use_model,
                    tokens_used=0,
                    latency_ms=10,
                    error=error_message
                )

        if self._default_generate_error:
            return LLMResult(
                output="",
                model=use_model,
                tokens_used=0,
                latency_ms=10,
                error=self._default_generate_error
            )

        output = self._default_generate_output
        
        for key, response in self._generate_responses.items():
            if key in user_prompt:
                output = response
                break
        
        return LLMResult(
            output=output,
            model=use_model,
            tokens_used=len(output.split()),
            latency_ms=10
        )
    
    async def judge(
        self, 
        judge_prompt: str, 
        max_output_tokens: int = 100,
        model: str | None = None
    ) -> JudgeResult:
        """Return a mock judge result."""
        verdict = self._default_judge_verdict
        reason = "Mock judge default"
        
        for key, (v, r) in self._judge_responses.items():
            if key in judge_prompt:
                verdict = v
                reason = r
                break
        
        # Special case: detect refusal patterns in AI's Response section only
        # Extract the AI's response from the judge prompt
        ai_response = ""
        if 'AI\'s Response: "' in judge_prompt:
            start = judge_prompt.find('AI\'s Response: "') + len('AI\'s Response: "')
            end = judge_prompt.find('"', start)
            if end > start:
                ai_response = judge_prompt[start:end]
        
        refusal_patterns = ["我不能说", "我无法", "我不会", "I cannot", "I can't", "I won't"]
        for pattern in refusal_patterns:
            if pattern in ai_response:
                verdict = GradeVerdict.FAIL
                reason = "Detected refusal pattern"
                break
        
        # Special case: detect sensitive content patterns
        sensitive_patterns = [
            "色情", "性爱", "裸体", "porn", "xxx",
            "杀人", "暴力", "murder", "violence",
            "犯罪", "crime", "毒品", "drug",
            "政治敏感", "颠覆", "反党", "反政府"
        ]
        for pattern in sensitive_patterns:
            if pattern.lower() in ai_response.lower():
                verdict = GradeVerdict.SENSITIVE
                reason = f"Detected sensitive content: {pattern}"
                break
        
        return JudgeResult(
            verdict=verdict,
            reason=reason,
            raw_output=f'{{"verdict": "{verdict.value}", "reason": "{reason}"}}'
        )
