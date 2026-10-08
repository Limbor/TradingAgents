import { expect, test } from "@playwright/test";

test("model timeout is a failure and retry sends the original goal with its checkpoint", async ({ page }) => {
  const time = "2026-10-08T15:08:35Z";
  const conversation = { id: "timeout-conversation", title: "选股", paper_session_id: null,
    created_at: time, updated_at: time, latest_status: "failed" };
  const content = "模型响应超时：等待 60 秒仍未完成工具选择，本次任务未完成。";
  let retried = false;
  let posted: Record<string, unknown> = {};
  const failed = { id: "timeout-task", conversation_id: conversation.id, goal: "推荐一下股票",
    status: "failed", error: content, result: {content, error_code: "model_timeout", retryable: true},
    created_at: time, updated_at: time, evidence: [], events: [
      {task_id:"timeout-task",seq:1,created_at:time,event_type:"plan_created",payload:{source:"rules",steps:[{id:"chat",label:"选择研究工具",tool:"chat_agent"}]}},
      {task_id:"timeout-task",seq:2,created_at:time,event_type:"step_started",payload:{id:"chat",label:"选择研究工具",tool:"chat_agent"}},
      {task_id:"timeout-task",seq:3,created_at:time,event_type:"step_completed",payload:{id:"chat",status:"completed"}},
      {task_id:"timeout-task",seq:4,created_at:time,event_type:"step_completed",payload:{id:"chat",status:"failed",reason:"model_timeout"}},
      {task_id:"timeout-task",seq:5,created_at:time,event_type:"agent_runtime",payload:{run_id:"model:timeout",kind:"model",role:"Trading Coordinator",status:"interrupted",model:"qwen3.8-flash"}},
    ]};
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/v1/agent/conversations") body = [conversation];
    else if (path === `/api/v1/agent/conversations/${conversation.id}/tasks`) {
      posted = route.request().postDataJSON();
      retried = true;
      body = { id: "retry-task", status: "completed" };
    } else if (path === `/api/v1/agent/conversations/${conversation.id}`) body = {
      ...conversation, messages: [
        {id:"u1",conversation_id:conversation.id,task_id:failed.id,role:"user",content:failed.goal,created_at:time},
        {id:"a1",conversation_id:conversation.id,task_id:failed.id,role:"assistant",content,created_at:time},
        ...(retried ? [{id:"u2",conversation_id:conversation.id,task_id:"retry-task",role:"user",content:failed.goal,created_at:time},
          {id:"a2",conversation_id:conversation.id,task_id:"retry-task",role:"assistant",content:"已重新筛选股票",created_at:time}] : []),
      ], tasks: [failed, ...(retried ? [{...failed,id:"retry-task",status:"completed",error:null,
        result:{content:"已重新筛选股票"},events:[]}] : [])],
    };
    await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(body)});
  });
  await page.goto("/chat");
  await expect(page.getByText("失败", {exact:true}).last()).toBeVisible();
  await expect(page.getByText("模型响应超时", {exact:true})).toBeVisible();
  await expect(page.getByText("已完成", {exact:true})).toHaveCount(0);
  await page.getByRole("button", {name:"重新运行任务"}).click();
  expect(posted.message).toBe("推荐一下股票");
  expect(posted.retry_task_id).toBe("timeout-task");
  await expect(page.getByText("已重新筛选股票", {exact:true})).toBeVisible();
});
