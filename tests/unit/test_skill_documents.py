"""Document loading, propagation, and packaging contracts without model requests."""
from pathlib import Path

import pytest
from pydantic import BaseModel

from tradingagents.core.agent_runtime import (
    AgentContext,
    RuntimeModel,
    current_context,
    use_context,
)
from tradingagents.core.research_context import research_policy_key
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata
from tradingagents.skills.documents import MAX_DOCUMENT_CHARS, load_skill_document, skill_contract
from tradingagents.skills.registry import SkillRegistry


@pytest.mark.parametrize('skill_id', ['stock_analysis', 'market_overview', 'market_scanner',
    'daily_pipeline', 'portfolio_management', 'position_advisor', 'risk_monitor',
    'daily_review', 'strategy_backtest', 'decision_audit'])
def test_all_builtin_documents_fit_progressive_contract(skill_id):
    doc = load_skill_document(skill_id)
    assert doc.skill_id == skill_id and doc.version == '1.0.0'
    assert len(doc.instructions) < MAX_DOCUMENT_CHARS
    assert len(doc.digest) == 64
    registry = SkillRegistry()
    registry.auto_discover()
    contract = skill_contract(registry.get(skill_id))
    import json
    assert len(json.dumps(contract, ensure_ascii=False)) < 16000
    assert contract['parameters'] == registry.get(skill_id).input_schema.model_json_schema()


@pytest.mark.parametrize('bad', ['../stock_analysis', '/tmp/evil', 'stock_analysis/SKILL.md'])
def test_document_paths_are_not_model_controlled(bad):
    with pytest.raises(ValueError):
        load_skill_document(bad)
    assert load_skill_document('external_plugin') is None


def test_skill_version_invalidates_research_reuse():
    config = {'agent_model': 'fixture', 'skill_document_hash': 'v1'}
    assert research_policy_key(config) != research_policy_key({**config, 'skill_document_hash': 'v2'})


@pytest.mark.asyncio
async def test_managed_execution_injects_one_active_document_and_restores_parent():
    class Params(BaseModel):
        value: str = 'hello'

    class Fixture(BaseSkill):
        metadata = SkillMetadata(id='stock_analysis', name='fixture', description='test', version='1')
        input_schema = Params
        output_schema = Params

        async def execute(self, params, config):
            context = current_context()
            assert context.skill_refs[0]['skill_id'] == 'stock_analysis'
            assert context.config['skill_document_hash'] == load_skill_document('stock_analysis').digest
            assert context.config['investment_style'] == 'medium_term'
            assert context.task_context['symbols'] == ['600487.SH']
            text = RuntimeModel._inject_context('研究', context)
            assert 'fundamentals' in text and '不是行情证据或操作授权' in text
            assert text.count('<active_skill_instructions>') == 1
            yield SkillEvent('skill_complete', {'status': 'success', 'value': params.value})

        async def cancel(self):
            pass

    from dataclasses import replace
    root = replace(AgentContext.root({}), task_context={'symbols': ['600487.SH']})
    with use_context(root):
        events = []
        async for event in Fixture().managed_execute(Params(), {'investment_style': 'medium_term'}):
            assert current_context() is root
            events.append(event)
        assert current_context() is root and not root.skill_prompt
    assert [event.event_type for event in events] == ['skill_progress', 'skill_complete']


def test_nested_workflows_use_managed_execution_and_docs_are_packaged():
    root = Path(__file__).resolve().parents[2]
    for name in ('daily_pipeline', 'daily_review'):
        source = (root / 'tradingagents/skills' / name / 'skill.py').read_text()
        assert 'skill.execute(' not in source
        assert 'skill.managed_execute(' in source
    assert '"tradingagents.skills" = ["*/SKILL.md"]' in (root / 'pyproject.toml').read_text()
    assert 'collect_data_files("tradingagents.skills"' in (root / 'packaging/tradingagents-backend.spec').read_text()
