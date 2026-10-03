"""Research continuity and scope changes, not keyword tool routing."""
from tradingagents.core.task_context import (
    accept_skill_context,
    apply_context_args,
    build_task_context,
)


def test_sector_followup_keeps_sector_not_assistant_stock_picks():
    previous = accept_skill_context(build_task_context('推荐近期板块'), 'market_overview',
                                   {'focus_industries': ['医药生物']})
    context = build_task_context('重新推荐一个', previous, previous_task_id='old')
    assert context['target'] == 'sector' and context['symbols'] == []
    assert context['industries'] == ['医药生物'] and context['filters']['limit'] == 1
    assert context['inherited_from'] == 'old' and context['evidence_refs'] == []


def test_new_topic_clears_prior_stock_and_filters():
    previous = accept_skill_context(build_task_context('只看主板股'), 'stock_analysis', {'ticker': '600487.SS'})
    current = build_task_context('推荐银行板块', previous)
    assert current['target'] == 'sector' and current['symbols'] == [] and current['filters'] == {}


def test_followup_horizon_dimensions_and_filters_are_kept_separately():
    previous = accept_skill_context(build_task_context('排除双创选股'), 'stock_analysis',
                                   {'ticker': '600487.SH', 'analysts': ['market']})
    current = build_task_context('它的营收呢，想做中线', previous)
    assert current['symbols'] == ['600487.SH'] and current['horizon'] == 'medium_term'
    assert current['dimensions'] == ['fundamentals']
    args = apply_context_args('daily_pipeline', {}, current)
    assert args['board_filter'] == 'main_board'
    assert 'board_filter' not in apply_context_args('stock_analysis', {}, current)
    assert apply_context_args('daily_pipeline', {'board_filter': 'all'}, current)['board_filter'] == 'main_board'
