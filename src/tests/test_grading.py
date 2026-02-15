"""Tests for grading module."""

import pytest
from ..domain.grading import KeywordGrader, LLMJudge, CompositeGrader
from ..domain.models import GradeVerdict
from ..adapters.llm_openai_compat.client import MockLLMClient


class TestKeywordGrader:
    """Tests for KeywordGrader."""
    
    def setup_method(self):
        self.grader = KeywordGrader()
    
    def test_substring_match_found(self):
        """Test substring match when target is present."""
        verdict, reason = self.grader.grade(
            output="This contains TARGET-PHRASE here",
            target_phrase="TARGET-PHRASE",
            match_policy="substring"
        )
        assert verdict == GradeVerdict.PASS
        assert "TARGET-PHRASE" in reason
    
    def test_substring_match_not_found(self):
        """Test substring match when target is absent."""
        verdict, reason = self.grader.grade(
            output="This does not contain the target",
            target_phrase="TARGET-PHRASE",
            match_policy="substring"
        )
        assert verdict == GradeVerdict.FAIL
    
    def test_exact_match_found(self):
        """Test exact word boundary match."""
        verdict, reason = self.grader.grade(
            output="The TARGET-PHRASE is here",
            target_phrase="TARGET-PHRASE",
            match_policy="exact"
        )
        assert verdict == GradeVerdict.PASS
    
    def test_case_insensitive_match(self):
        """Test case-insensitive matching."""
        verdict, reason = self.grader.grade(
            output="This has target-phrase in lowercase",
            target_phrase="TARGET-PHRASE",
            match_policy="case_insensitive"
        )
        assert verdict == GradeVerdict.PASS
    
    def test_regex_match(self):
        """Test regex matching."""
        verdict, reason = self.grader.grade(
            output="Version 2026.0.0 released",
            target_phrase=r"\d{4}\.\d+\.\d+",
            match_policy="regex"
        )
        assert verdict == GradeVerdict.PASS
    
    def test_regex_match_not_found(self):
        """Test regex matching when pattern not found."""
        verdict, reason = self.grader.grade(
            output="No version here",
            target_phrase=r"\d{4}\.\d+\.\d+",
            match_policy="regex"
        )
        assert verdict == GradeVerdict.FAIL


class TestLLMJudge:
    """Tests for LLMJudge."""
    
    @pytest.mark.asyncio
    async def test_judge_pass(self):
        """Test judge returns PASS for valid output."""
        mock_llm = MockLLMClient()
        mock_llm.set_default_judge_verdict(GradeVerdict.PASS)
        
        judge = LLMJudge(mock_llm)
        result = await judge.judge(
            level_id=1,
            level_intro="Test intro",
            target_phrase="TARGET",
            user_prompt="Please output TARGET",
            llm_output="Here is TARGET as requested"
        )
        
        assert result.verdict == GradeVerdict.PASS
    
    @pytest.mark.asyncio
    async def test_judge_fail_on_refusal(self):
        """Test judge returns FAIL on refusal pattern."""
        mock_llm = MockLLMClient()
        
        judge = LLMJudge(mock_llm)
        result = await judge.judge(
            level_id=1,
            level_intro="Test intro",
            target_phrase="TARGET",
            user_prompt="Please output TARGET",
            llm_output="我不能说 TARGET"
        )
        
        assert result.verdict == GradeVerdict.FAIL
    
    @pytest.mark.asyncio
    async def test_judge_sensitive_content(self):
        """Test judge returns SENSITIVE for harmful content."""
        mock_llm = MockLLMClient()
        mock_llm.set_default_judge_verdict(GradeVerdict.PASS)  # Would pass, but sensitive
        
        judge = LLMJudge(mock_llm)
        result = await judge.judge(
            level_id=1,
            level_intro="Test intro",
            target_phrase="TARGET",
            user_prompt="Generate content",
            llm_output="TARGET 色情内容在此"
        )
        
        assert result.verdict == GradeVerdict.SENSITIVE


class TestCompositeGrader:
    """Tests for CompositeGrader."""
    
    @pytest.fixture
    def mock_llm(self):
        """Create mock LLM client."""
        return MockLLMClient()
    
    @pytest.fixture
    def grader(self, mock_llm):
        """Create composite grader."""
        return CompositeGrader(mock_llm)
    
    @pytest.mark.asyncio
    async def test_pass_when_both_pass(self, grader, content_provider):
        """Test PASS when both keyword and judge pass."""
        grader.llm_judge.llm_client.set_default_judge_verdict(GradeVerdict.PASS)
        
        level = content_provider.get_level(1)
        result = await grader.grade(
            level_config=level,
            user_prompt="Generate TARGET-PHRASE-1",
            llm_output="Output: TARGET-PHRASE-1"
        )
        
        assert result.keyword_verdict == GradeVerdict.PASS
        assert result.judge_verdict == GradeVerdict.PASS
        assert result.final_verdict == GradeVerdict.PASS
    
    @pytest.mark.asyncio
    async def test_fail_when_keyword_fails(self, grader, content_provider):
        """Test FAIL when keyword doesn't match."""
        level = content_provider.get_level(1)
        result = await grader.grade(
            level_config=level,
            user_prompt="Say something",
            llm_output="Something without the target"
        )
        
        assert result.keyword_verdict == GradeVerdict.FAIL
        assert result.final_verdict == GradeVerdict.FAIL
    
    @pytest.mark.asyncio
    async def test_fail_when_judge_fails(self, grader, content_provider):
        """Test FAIL when judge fails (e.g., refusal)."""
        grader.llm_judge.llm_client.set_judge_response(
            "我不能说",
            GradeVerdict.FAIL,
            "Detected refusal"
        )
        
        level = content_provider.get_level(1)
        result = await grader.grade(
            level_config=level,
            user_prompt="Generate TARGET-PHRASE-1",
            llm_output="我不能说 TARGET-PHRASE-1"
        )
        
        assert result.keyword_verdict == GradeVerdict.PASS  # Keyword matches
        assert result.judge_verdict == GradeVerdict.FAIL    # But judge fails
        assert result.final_verdict == GradeVerdict.FAIL
    
    @pytest.mark.asyncio
    async def test_sensitive_verdict(self, grader, content_provider):
        """Test SENSITIVE verdict when content is harmful."""
        level = content_provider.get_level(1)
        result = await grader.grade(
            level_config=level,
            user_prompt="Generate TARGET-PHRASE-1",
            llm_output="TARGET-PHRASE-1 色情相关内容"
        )
        
        assert result.keyword_verdict == GradeVerdict.PASS
        assert result.judge_verdict == GradeVerdict.SENSITIVE
        assert result.final_verdict == GradeVerdict.SENSITIVE
    
    @pytest.mark.asyncio
    async def test_judge_parse_error_flag(self, grader, content_provider):
        """Test judge_parse_error is detected."""
        # Force a specific judge response with error field
        grader.llm_judge.llm_client.set_default_judge_verdict(GradeVerdict.PASS)
        
        level = content_provider.get_level(1)
        result = await grader.grade(
            level_config=level,
            user_prompt="Generate TARGET-PHRASE-1",
            llm_output="Output: TARGET-PHRASE-1"
        )
        
        # Normal result should not have parse error
        assert result.judge_parse_error is False


class TestPerLevelJudgeModel:
    """Tests for per-level judge model configuration."""

    @pytest.mark.asyncio
    async def test_judge_model_passed_to_llm_client(self, content_provider):
        """Judge model from level config should be passed to LLM client."""
        mock_llm = MockLLMClient()
        mock_llm.set_default_judge_verdict(GradeVerdict.PASS)

        # Track the model passed to judge
        called_models = []
        original_judge = mock_llm.judge

        async def tracking_judge(judge_prompt, max_output_tokens=100, model=None):
            called_models.append(model)
            return await original_judge(judge_prompt, max_output_tokens, model)

        mock_llm.judge = tracking_judge

        judge = LLMJudge(mock_llm)
        await judge.judge(
            level_id=1,
            level_intro="Test",
            target_phrase="TARGET",
            user_prompt="test",
            llm_output="TARGET output",
            judge_model="custom-judge-model"
        )

        assert called_models == ["custom-judge-model"]

    @pytest.mark.asyncio
    async def test_judge_model_none_uses_default(self, content_provider):
        """When judge_model is None, LLM client should use its default."""
        mock_llm = MockLLMClient()
        mock_llm.set_default_judge_verdict(GradeVerdict.PASS)

        called_models = []
        original_judge = mock_llm.judge

        async def tracking_judge(judge_prompt, max_output_tokens=100, model=None):
            called_models.append(model)
            return await original_judge(judge_prompt, max_output_tokens, model)

        mock_llm.judge = tracking_judge

        judge = LLMJudge(mock_llm)
        await judge.judge(
            level_id=1,
            level_intro="Test",
            target_phrase="TARGET",
            user_prompt="test",
            llm_output="TARGET output",
            judge_model=None
        )

        assert called_models == [None]

    @pytest.mark.asyncio
    async def test_composite_grader_passes_judge_model(self, content_provider):
        """CompositeGrader should pass level's judge_model to LLM judge."""
        mock_llm = MockLLMClient()
        mock_llm.set_default_judge_verdict(GradeVerdict.PASS)

        called_models = []
        original_judge = mock_llm.judge

        async def tracking_judge(judge_prompt, max_output_tokens=100, model=None):
            called_models.append(model)
            return await original_judge(judge_prompt, max_output_tokens, model)

        mock_llm.judge = tracking_judge

        grader = CompositeGrader(mock_llm)

        # Level 1 has judge_model="judge-model-lv1" in test config
        level = content_provider.get_level(1)
        assert level.grading.judge.judge_model == "judge-model-lv1"

        await grader.grade(
            level_config=level,
            user_prompt="Generate TARGET-PHRASE-1",
            llm_output="Output: TARGET-PHRASE-1"
        )

        assert called_models == ["judge-model-lv1"]

    @pytest.mark.asyncio
    async def test_composite_grader_no_judge_model_fallback(self, content_provider):
        """When level has no judge_model, None should be passed (uses default)."""
        mock_llm = MockLLMClient()
        mock_llm.set_default_judge_verdict(GradeVerdict.PASS)

        called_models = []
        original_judge = mock_llm.judge

        async def tracking_judge(judge_prompt, max_output_tokens=100, model=None):
            called_models.append(model)
            return await original_judge(judge_prompt, max_output_tokens, model)

        mock_llm.judge = tracking_judge

        grader = CompositeGrader(mock_llm)

        # Level 2 has no judge_model in test config
        level = content_provider.get_level(2)
        assert level.grading.judge.judge_model is None

        await grader.grade(
            level_config=level,
            user_prompt="Generate TARGET-PHRASE-2",
            llm_output="Output: TARGET-PHRASE-2"
        )

        assert called_models == [None]
