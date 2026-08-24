from __future__ import annotations


def login_html() -> str:
    return _page(
        title="EchoWeave 登录",
        body="""
  <header>
    <h1>EchoWeave 登录</h1>
  </header>
  <main class="login-shell">
    <section class="panel login-card">
      <h2>账号登录</h2>
      <div id="error" class="error"></div>
      <label>用户名<input id="login-username" autocomplete="username" autofocus></label>
      <label>密码<input id="login-password" type="password" autocomplete="current-password"></label>
      <div class="toolbar">
        <button class="primary" onclick="login()">登录</button>
        <button onclick="registerUser()">注册/初始化</button>
      </div>
      <details>
        <summary>使用 Webhook 访问密码登录</summary>
        <label>访问密码<input id="login-token" type="password" autocomplete="current-password"></label>
        <button onclick="legacyLogin()">访问密码登录</button>
      </details>
    </section>
  </main>
""",
        script=_login_script(),
    )


def admin_html() -> str:
    return _page(
        title="EchoWeave Admin 管理端",
        body="""
  <header>
    <h1>EchoWeave Admin 管理端</h1>
    <nav><a id="user-link" href="/user">用户端</a><button onclick="logout()">登出</button><button class="primary" onclick="refreshAll()">刷新</button></nav>
  </header>
  <main class="admin-main">
    <section class="status">
      <div class="metric">服务<strong id="service">-</strong></div>
      <div class="metric">待审批<strong id="pending">0</strong></div>
      <div class="metric">最近审批<strong id="recent">0</strong></div>
    </section>
    <div id="error" class="error"></div>

    <section class="admin-section">
      <div class="section-head">
        <div><h2>审批</h2><p>查看、通过、拒绝、撤销或重试需要人工确认的命令。</p></div>
      </div>
      <table>
        <thead><tr><th>ID</th><th>状态</th><th>命令</th><th>原因</th><th>会话</th><th>操作</th></tr></thead>
        <tbody id="approvals"><tr><td colspan="6" class="muted">加载中...</td></tr></tbody>
      </table>
    </section>

    <section class="admin-section">
      <div class="section-head">
        <div><h2>模型与 API Key</h2><p>这里决定用户端可选模型、默认模型，以及真实 LLM 的密钥来源。</p></div>
        <button class="primary" onclick="openJsonEditor('model_profiles')">配置模型与密钥</button>
      </div>
      <section class="grid">
        <label><span class="field-title">默认模型 profile <button type="button" class="help" data-tip="新会话默认使用的模型配置。用户端下拉框也来自 Model profiles。">?</button></span><select id="cfg-default-profile"></select></label>
        <label><span class="field-title">兜底 Provider <button type="button" class="help" data-tip="只有没有可用 model profile 时才使用。一般优先配置 Model profiles。">?</button></span><select id="cfg-provider" onchange="refreshModelChoices()"></select></label>
        <label><span class="field-title">兜底 Model <button type="button" class="help" data-tip="兜底模型名称。profile 没指定 model 时也会回退到这里。选择“自定义”后可填写任意模型 ID。">?</button></span><select id="cfg-model" onchange="syncCustomSelect('cfg-model')"></select></label>
        <label id="cfg-model-custom-wrap" class="hidden"><span class="field-title">自定义 Model</span><input id="cfg-model-custom" placeholder="例如 deepseek-chat" oninput="markCustomSelect('cfg-model')"></label>
        <div class="json-card">
          <div><strong>Model profiles</strong><span>用户端可选择的模型列表，支持直接保存 API key 或使用环境变量</span></div>
          <button onclick="openJsonEditor('model_profiles')">打开模型配置</button>
          <div id="cfg-model-profiles-summary" class="hint-line">未加载</div>
          <textarea id="cfg-model-profiles" class="json-storage" aria-hidden="true"></textarea>
        </div>
        <div class="json-card">
          <div><strong>AI providers</strong><span>注册 OpenAI-compatible 平台，如 LM Studio、OpenRouter、SiliconFlow</span></div>
          <button onclick="openJsonEditor('ai_providers')">打开 provider 配置</button>
          <div id="cfg-ai-providers-summary" class="hint-line">未加载</div>
          <textarea id="cfg-ai-providers" class="json-storage" aria-hidden="true"></textarea>
        </div>
      </section>
    </section>

    <section class="admin-section">
      <div class="section-head">
        <div><h2>RAG 检索</h2><p>配置是否默认启用 RAG、pgvector 混合检索权重、多 query 改写和重排。</p></div>
      </div>
      <section class="grid">
        <label><span class="field-title">默认 RAG <button type="button" class="help" data-tip="新会话是否默认启用 RAG；用户端仍可按会话开关。">?</button></span><select id="cfg-rag-enabled"><option value="false">关闭</option><option value="true">开启</option></select></label>
        <label><span class="field-title">RAG backend <button type="button" class="help" data-tip="pgvector_hybrid_bgem3 使用 PostgreSQL pgvector + BM25 混合检索。">?</button></span><select id="cfg-rag-backend"><option value="pgvector_hybrid_bgem3">pgvector_hybrid_bgem3</option><option value="lexical">lexical</option></select></label>
        <label><span class="field-title">pgvector table <button type="button" class="help" data-tip="RAG chunk 在 PostgreSQL 中保存的表名。选择“自定义”后可填写任意表名。">?</button></span><select id="cfg-rag-table" onchange="syncCustomSelect('cfg-rag-table')"></select></label>
        <label id="cfg-rag-table-custom-wrap" class="hidden"><span class="field-title">自定义表名</span><input id="cfg-rag-table-custom" placeholder="echoweave_rag_chunks" oninput="markCustomSelect('cfg-rag-table')"></label>
        <label><span class="field-title">向量权重 <button type="button" class="help" data-tip="混合检索中向量相似度分数权重。">?</button></span><input id="cfg-rag-vector" type="number" step="0.01" min="0"></label>
        <label><span class="field-title">BM25 权重 <button type="button" class="help" data-tip="混合检索中关键词 BM25 分数权重。">?</button></span><input id="cfg-rag-bm25" type="number" step="0.01" min="0"></label>
        <label><span class="field-title">Query rewrite <button type="button" class="help" data-tip="检索前是否把用户问题改写成多个查询，提高召回。">?</button></span><select id="cfg-rag-query-rewrite"><option value="false">关闭</option><option value="true">开启</option></select></label>
        <label><span class="field-title">Rewrite strategy <button type="button" class="help" data-tip="查询改写策略，当前内置 local_multi_query。">?</button></span><select id="cfg-rag-query-rewrite-strategy"><option value="local_multi_query">local_multi_query</option><option value="noop">noop</option></select></label>
        <label><span class="field-title">Max queries <button type="button" class="help" data-tip="一次问题最多生成多少个检索查询。">?</button></span><input id="cfg-rag-query-rewrite-max" type="number" min="1"></label>
        <label><span class="field-title">Rerank <button type="button" class="help" data-tip="检索后是否对候选片段重排。">?</button></span><select id="cfg-rag-rerank"><option value="false">关闭</option><option value="true">开启</option></select></label>
        <label><span class="field-title">Rerank strategy <button type="button" class="help" data-tip="重排策略，当前内置 bm25。">?</button></span><select id="cfg-rag-rerank-strategy"><option value="bm25">bm25</option><option value="noop">noop</option></select></label>
        <label><span class="field-title">候选倍数 <button type="button" class="help" data-tip="重排前扩大候选集的倍数，值越大召回越宽但更慢。">?</button></span><input id="cfg-rag-rerank-multiplier" type="number" min="1"></label>
        <label><span class="field-title">原始分权重 <button type="button" class="help" data-tip="重排时保留原始检索分数的权重。">?</button></span><input id="cfg-rag-rerank-original" type="number" step="0.01" min="0"></label>
        <label><span class="field-title">BM25 重排权重 <button type="button" class="help" data-tip="重排时 BM25 文本匹配分数的权重。">?</button></span><input id="cfg-rag-rerank-bm25" type="number" step="0.01" min="0"></label>
      </section>
    </section>

    <section class="admin-section">
      <div class="section-head">
        <div><h2>运行、权限与 Harness</h2><p>配置会话沙盒、管理员、全局 skill、审批超时和 harness 策略。</p></div>
      </div>
      <section class="grid">
        <label><span class="field-title">Sandbox root <button type="button" class="help" data-tip="每个会话独立沙盒的根目录。未绑定真实仓库时，文件操作都限制在这里。">?</button></span><input id="cfg-sandbox-root"></label>
        <label><span class="field-title">审批超时秒数 <button type="button" class="help" data-tip="命令审批 pending 多久后自动过期。">?</button></span><input id="cfg-approval-timeout" type="number" min="1"></label>
        <div class="picker-card">
          <div><strong>全局 skills</strong><span>所有会话默认启用的 skill</span></div>
          <button type="button" onclick="openListPicker('global_enabled_skills')">选择 skills</button>
          <div id="cfg-global-skills-summary" class="hint-line">未加载</div>
          <textarea id="cfg-global-skills" class="json-storage" aria-hidden="true"></textarea>
        </div>
        <div class="picker-card">
          <div><strong>管理员</strong><span>允许审批、绑定真实目录和修改配置的用户 ID</span></div>
          <button type="button" onclick="openListPicker('admins')">选择管理员</button>
          <div id="cfg-admins-summary" class="hint-line">未加载</div>
          <textarea id="cfg-admins" class="json-storage" aria-hidden="true"></textarea>
        </div>
        <label><span class="field-title">Harness audit <button type="button" class="help" data-tip="是否写入结构化审计日志。">?</button></span><select id="cfg-harness-audit-enabled"><option value="true">开启</option><option value="false">关闭</option></select></label>
        <label><span class="field-title">Harness audit path <button type="button" class="help" data-tip="审计日志 JSONL 文件路径。">?</button></span><input id="cfg-harness-audit-path"></label>
        <div class="json-card">
          <div><strong>Harness policy</strong><span>工具、路径、命令、模型、RAG 策略</span></div>
          <button onclick="openJsonEditor('harness_policy')">打开策略配置</button>
          <div id="cfg-harness-policy-summary" class="hint-line">未加载</div>
          <textarea id="cfg-harness-policy" class="json-storage" aria-hidden="true"></textarea>
        </div>
      </section>
    </section>

    <section class="save-bar">
      <div class="json-card">
        <div><strong>保存配置</strong><span>修改会立即写入运行时；如果配置文件存在，也会持久化到本地配置。</span></div>
        <button class="primary" onclick="saveConfig()">保存全部配置</button>
      </div>
    </section>
  </main>
  <div id="json-modal" class="modal hidden">
    <section class="modal-card">
      <header><h2 id="json-modal-title">配置编辑</h2><button onclick="closeJsonEditor()">关闭</button></header>
      <div id="json-form" class="form-grid"></div>
      <label>JSON 预览与高级编辑<textarea id="json-editor" oninput="syncJsonToForm()"></textarea></label>
      <div id="json-error" class="error"></div>
      <div class="toolbar">
        <button onclick="applyJsonEditor()">应用到配置</button>
        <button class="primary" onclick="applyJsonEditor(); saveConfig()">应用并保存</button>
      </div>
    </section>
  </div>
  <div id="list-modal" class="modal hidden">
    <section class="modal-card compact-modal">
      <header><h2 id="list-modal-title">选择</h2><button onclick="closeListPicker()">关闭</button></header>
      <div id="list-picker" class="choice-list"></div>
      <div class="inline-add">
        <input id="list-new-value" placeholder="新增自定义项">
        <button type="button" onclick="addListPickerValue()">新增</button>
      </div>
      <div class="toolbar">
        <button onclick="closeListPicker()">取消</button>
        <button class="primary" onclick="applyListPicker()">应用</button>
      </div>
    </section>
  </div>
""",
        script=_admin_script(),
    )


def user_html() -> str:
    return _page(
        title="EchoWeave AI Coding 用户端",
        body="""
  <header>
    <h1>EchoWeave AI Coding 用户端</h1>
    <nav><a id="admin-link" href="/admin">管理端</a><button onclick="logout()">登出</button><button class="primary" onclick="sendMessage()">发送</button></nav>
  </header>
  <main class="workspace">
    <aside class="panel side">
      <h2>会话</h2>
      <label>会话 ID<input id="chat-conversation" value="web-coding" oninput="refreshCapabilities()"></label>
      <label>用户 ID<input id="chat-sender" value="web-admin" oninput="refreshCapabilities()"></label>
      <label>平台<input id="chat-platform" value="web-user" oninput="refreshCapabilities()"></label>
      <div class="toolbar">
        <button onclick="runAction('/status', '刷新会话状态')">刷新状态</button>
        <button onclick="runAction('/help', '查看帮助')">帮助</button>
        <button onclick="runAction('/approvals', '查看审批')">审批</button>
      </div>

      <h2>工作区</h2>
      <label>本地路径<input id="workspace-path" placeholder="D:\\develop\\agent\\EchoWeave"></label>
      <div class="toolbar">
        <button onclick="bindWorkspace()">绑定路径</button>
        <button onclick="runAction('/unbind', '回到会话沙盒')">回到沙盒</button>
      </div>

      <h2>模型</h2>
      <label>当前会话模型<select id="model-select"></select></label>
      <div id="model-details" class="hint-line">加载模型配置...</div>
      <button class="primary wide" onclick="applySelectedModel()">应用模型</button>

      <h2>RAG</h2>
      <label class="check-row"><input id="rag-toggle" type="checkbox" onchange="toggleRag()"> <span>为当前会话启用检索增强</span></label>
      <div id="rag-details" class="hint-line">加载 RAG 状态...</div>
      <button onclick="runAction('/rag index', '索引当前工作区')">索引当前工作区</button>

      <h2>Skills</h2>
      <div id="skills-list" class="choice-list"><div class="muted">加载 skills...</div></div>

      <h2>事件流</h2>
      <div id="stream-state" class="badge muted">未连接</div>
      <div id="events" class="events-list">
        <div class="event muted">等待 SSE 事件...</div>
      </div>
    </aside>

    <section class="panel chat">
      <div id="error" class="error"></div>
      <div class="chat-status">
        <span class="badge">当前会话 <strong id="active-conversation">web-coding</strong></span>
        <span class="badge">模型 <strong id="active-model">未知</strong></span>
        <span class="badge">RAG <strong id="active-rag">未知</strong></span>
      </div>
      <div id="messages" class="messages">
        <div class="msg assistant">你好，我是 EchoWeave。这里是独立 Web AI Coding 工作台，可以直接让我读写当前会话沙盒中的文件、执行需要审批的命令、切换模型或使用 RAG。</div>
      </div>
      <div class="composer">
        <textarea id="chat-input" placeholder="例如：创建一个 Python 脚本统计当前目录下的文件；或输入 /status"></textarea>
        <button class="primary" onclick="sendMessage()">发送</button>
      </div>
    </section>
  </main>
""",
        script=_user_script(),
    )


def _page(*, title: str, body: str, script: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light; font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif; }}
    body {{ margin: 0; background: #f6f8fb; color: #17202a; }}
    header {{ padding: 16px 24px; background: #18222d; color: white; display: flex; align-items: center; justify-content: space-between; gap: 16px; }}
    main {{ max-width: 1220px; margin: 0 auto; padding: 24px; }}
    .admin-main {{ display: grid; gap: 16px; }}
    h1 {{ margin: 0; font-size: 22px; }}
    h2 {{ margin: 22px 0 10px; font-size: 16px; }}
    nav {{ display: flex; gap: 10px; align-items: center; }}
    nav a {{ color: white; text-decoration: none; border: 1px solid rgba(255,255,255,0.45); padding: 7px 10px; border-radius: 6px; }}
    button {{ border: 1px solid #b6c2cf; background: white; color: #18222d; padding: 7px 10px; border-radius: 6px; cursor: pointer; font: inherit; }}
    button.primary {{ background: #1f6feb; color: white; border-color: #1f6feb; }}
    button.danger {{ color: #b42318; border-color: #f3b7b0; }}
    button.wide {{ width: 100%; }}
    button.help {{ border-radius: 999px; width: 20px; height: 20px; padding: 0; font-size: 12px; color: #526173; border-color: #cbd5e1; display: inline-grid; place-items: center; flex: 0 0 auto; }}
    .tooltip-popover {{ position: fixed; z-index: 30; max-width: min(280px, 70vw); background: #111827; color: #fff; border-radius: 6px; padding: 8px 10px; box-shadow: 0 10px 28px rgba(15,23,42,0.22); font-size: 12px; line-height: 1.45; text-align: left; pointer-events: none; opacity: 0; transform: translateY(4px); transition: opacity 0.08s ease, transform 0.08s ease; }}
    .tooltip-popover.visible {{ opacity: 1; transform: translateY(0); }}
    .status {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }}
    .metric, .panel {{ background: white; border: 1px solid #d9e0e7; border-radius: 8px; padding: 14px; }}
    .metric strong {{ display: block; font-size: 26px; margin-top: 6px; }}
    .admin-section {{ background: white; border: 1px solid #d9e0e7; border-radius: 8px; padding: 14px; overflow: visible; }}
    .section-head {{ display: flex; justify-content: space-between; align-items: start; gap: 12px; margin-bottom: 12px; }}
    .section-head h2 {{ margin: 0 0 4px; }}
    .section-head p {{ margin: 0; color: #637083; font-size: 13px; line-height: 1.45; }}
    .save-bar {{ position: sticky; bottom: 0; z-index: 5; background: rgba(246,248,251,0.92); backdrop-filter: blur(8px); padding: 8px 0 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; background: white; border: 1px solid #d9e0e7; border-radius: 8px; padding: 14px; }}
    .admin-section .grid {{ border: 0; padding: 0; background: transparent; }}
    label {{ display: grid; gap: 6px; font-size: 13px; color: #637083; margin-bottom: 10px; }}
    .field-title {{ display: inline-flex; align-items: center; gap: 6px; }}
    input, select, textarea {{ border: 1px solid #c6d0dc; border-radius: 6px; padding: 8px; font: inherit; color: #17202a; background: white; min-width: 0; }}
    textarea {{ min-height: 92px; resize: vertical; grid-column: 1 / -1; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #d9e0e7; }}
    th, td {{ text-align: left; padding: 10px; border-bottom: 1px solid #edf1f5; vertical-align: top; font-size: 14px; }}
    th {{ background: #eef3f7; font-weight: 600; }}
    code {{ background: #edf1f5; padding: 2px 4px; border-radius: 4px; }}
    .actions, .toolbar {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }}
    .hint-line {{ color: #637083; font-size: 12px; line-height: 1.45; margin: -4px 0 10px; }}
    .check-row {{ display: flex; grid-template-columns: none; align-items: center; gap: 8px; color: #17202a; }}
    .check-row input {{ width: 16px; height: 16px; }}
    .choice-list {{ display: grid; gap: 6px; margin: 8px 0 12px; }}
    .choice-item {{ border: 1px solid #d9e0e7; background: #fbfcfe; border-radius: 6px; padding: 8px; display: grid; gap: 3px; }}
    .choice-item label {{ display: flex; align-items: center; gap: 8px; color: #17202a; margin: 0; }}
    .choice-item small {{ color: #637083; line-height: 1.35; }}
    .json-card {{ grid-column: 1 / -1; border: 1px solid #d9e0e7; border-radius: 8px; padding: 10px; display: grid; gap: 8px; background: #fbfcfe; }}
    .json-card > div {{ display: flex; align-items: baseline; gap: 8px; }}
    .json-card span {{ color: #637083; font-size: 12px; }}
    .json-card textarea {{ min-height: 120px; font-family: Consolas, monospace; }}
    .json-storage {{ display: none; }}
    .picker-card {{ border: 1px solid #d9e0e7; border-radius: 8px; padding: 10px; display: grid; gap: 8px; background: #fbfcfe; }}
    .picker-card > div:first-child {{ display: flex; align-items: baseline; gap: 8px; }}
    .picker-card span {{ color: #637083; font-size: 12px; }}
    .hidden {{ display: none !important; }}
    .modal {{ position: fixed; inset: 0; background: rgba(15,23,42,0.45); display: grid; place-items: center; padding: 20px; z-index: 10; }}
    .modal.hidden {{ display: none; }}
    .modal-card {{ background: white; width: min(920px, calc(100vw - 32px)); max-height: calc(100vh - 40px); overflow: auto; border-radius: 8px; border: 1px solid #d9e0e7; padding: 14px; box-shadow: 0 18px 45px rgba(15,23,42,0.25); }}
    .compact-modal {{ width: min(560px, calc(100vw - 32px)); }}
    .modal-card header {{ background: white; color: #17202a; padding: 0 0 12px; border-bottom: 1px solid #edf1f5; }}
    .form-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; margin: 12px 0; }}
    .form-grid .full {{ grid-column: 1 / -1; }}
    .inline-add {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; margin-top: 10px; }}
    .muted {{ color: #637083; }}
    .error {{ color: #b42318; white-space: pre-wrap; margin: 10px 0; }}
    .workspace {{ max-width: none; display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 14px; height: calc(100vh - 73px); box-sizing: border-box; }}
    .login-shell {{ min-height: calc(100vh - 73px); display: grid; place-items: center; }}
    .login-card {{ width: min(420px, calc(100vw - 32px)); box-sizing: border-box; }}
    .side {{ overflow: auto; }}
    .chat {{ display: grid; grid-template-rows: 1fr auto; min-height: 0; }}
    .messages {{ overflow: auto; display: flex; flex-direction: column; gap: 10px; padding-right: 4px; }}
    .msg {{ border: 1px solid #d9e0e7; border-radius: 8px; padding: 10px; white-space: pre-wrap; word-break: break-word; }}
    .msg.user {{ align-self: flex-end; max-width: 82%; background: #eaf2ff; border-color: #b9d3ff; }}
    .msg.assistant {{ align-self: flex-start; max-width: 88%; background: #ffffff; }}
    .chat-status {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }}
    .badge {{ display: inline-flex; align-items: center; gap: 5px; border: 1px solid #d9e0e7; border-radius: 999px; padding: 4px 8px; background: #f9fbfd; font-size: 12px; }}
    .events-list {{ border: 1px solid #d9e0e7; border-radius: 8px; background: #fbfcfe; max-height: 180px; overflow: auto; padding: 8px; display: grid; gap: 6px; }}
    .event {{ font-size: 12px; border-bottom: 1px solid #edf1f5; padding-bottom: 5px; word-break: break-word; }}
    .event:last-child {{ border-bottom: 0; padding-bottom: 0; }}
    .composer {{ display: grid; grid-template-columns: 1fr auto; gap: 10px; margin-top: 12px; align-items: end; }}
    .composer textarea {{ min-height: 76px; }}
    @media (max-width: 900px) {{ .workspace {{ grid-template-columns: 1fr; height: auto; }} main {{ padding: 16px; }} .composer {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
{body}
  <script>
{script}
  </script>
</body>
</html>"""


def _common_script() -> str:
    return """
    let helpTooltip = null;
    async function api(path, options = {}) {
      const res = await fetch(path, { credentials: "same-origin", ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
      if (res.status === 401) {
        location.href = "/login?next=" + encodeURIComponent(location.pathname);
        throw new Error("Unauthorized");
      }
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    }
    async function logout() {
      await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
      location.href = "/login";
    }
    function valueOf(id) {
      const el = document.getElementById(id);
      return el ? el.value.trim() : "";
    }
    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
    function installHelpTooltips() {
      const buttons = document.querySelectorAll("button.help[data-tip]");
      if (!buttons.length) return;
      helpTooltip = document.createElement("div");
      helpTooltip.className = "tooltip-popover";
      document.body.appendChild(helpTooltip);
      for (const button of buttons) {
        button.addEventListener("click", event => {
          event.preventDefault();
          event.stopPropagation();
        });
        button.addEventListener("mouseenter", showHelpTooltip);
        button.addEventListener("focus", showHelpTooltip);
        button.addEventListener("mouseleave", hideHelpTooltip);
        button.addEventListener("blur", hideHelpTooltip);
      }
    }
    function showHelpTooltip(event) {
      if (!helpTooltip) return;
      const button = event.currentTarget;
      helpTooltip.textContent = button.getAttribute("data-tip") || "";
      helpTooltip.classList.add("visible");
      const rect = button.getBoundingClientRect();
      const tipRect = helpTooltip.getBoundingClientRect();
      const left = Math.max(8, Math.min(window.innerWidth - tipRect.width - 8, rect.left + rect.width / 2 - tipRect.width / 2));
      const top = Math.min(window.innerHeight - tipRect.height - 8, rect.bottom + 8);
      helpTooltip.style.left = left + "px";
      helpTooltip.style.top = top + "px";
    }
    function hideHelpTooltip() {
      if (helpTooltip) helpTooltip.classList.remove("visible");
    }
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", installHelpTooltips);
    } else {
      installHelpTooltips();
    }
"""


def _login_script() -> str:
    return _common_script() + """
    async function login() {
      document.getElementById("error").textContent = "";
      try {
        const res = await fetch("/api/login", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            username: valueOf("login-username"),
            password: valueOf("login-password")
          })
        });
        if (!res.ok) throw new Error(await res.text());
        const params = new URLSearchParams(location.search);
        location.href = params.get("next") || "/";
      } catch (err) {
        document.getElementById("error").textContent = "登录失败：" + String(err.message || err);
      }
    }
    async function registerUser() {
      document.getElementById("error").textContent = "";
      try {
        const res = await fetch("/api/register", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            username: valueOf("login-username"),
            password: valueOf("login-password")
          })
        });
        if (!res.ok) throw new Error(await res.text());
        const params = new URLSearchParams(location.search);
        location.href = params.get("next") || "/";
      } catch (err) {
        document.getElementById("error").textContent = "注册失败：" + String(err.message || err);
      }
    }
    async function legacyLogin() {
      document.getElementById("error").textContent = "";
      try {
        const res = await fetch("/api/login", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: valueOf("login-token") })
        });
        if (!res.ok) throw new Error(await res.text());
        const params = new URLSearchParams(location.search);
        location.href = params.get("next") || "/";
      } catch (err) {
        document.getElementById("error").textContent = "访问密码登录失败：" + String(err.message || err);
      }
    }
    for (const id of ["login-username", "login-password"]) {
      document.getElementById(id).addEventListener("keydown", event => {
        if (event.key === "Enter") login();
      });
    }
    document.getElementById("login-token").addEventListener("keydown", event => {
      if (event.key === "Enter") legacyLogin();
    });
"""


def _admin_script() -> str:
    return _common_script() + """
    let currentConfig = {};
    let currentCapabilities = {};
    let editorTarget = null;
    let editorSyncing = false;
    let listPickerTarget = null;
    const KEEP_API_KEY = "__ECHOWEAVE_KEEP_EXISTING_API_KEY__";

    async function refreshAll() {
      document.getElementById("error").textContent = "";
      try {
        const status = await api("/api/status");
        document.getElementById("service").textContent = status.service || "EchoWeave";
        document.getElementById("pending").textContent = status.approvals?.pending ?? 0;
        try { currentCapabilities = await api("/api/capabilities?conversation_id=web-coding&sender_id=web-admin"); }
        catch { currentCapabilities = {}; }
        if (status.config) renderConfig(status.config);
        const approvals = await api("/api/approvals");
        renderApprovals(approvals.approvals || []);
      } catch (err) {
        document.getElementById("error").textContent = String(err);
      }
    }
    function renderApprovals(items) {
      document.getElementById("recent").textContent = items.length;
      const tbody = document.getElementById("approvals");
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="muted">暂无审批</td></tr>';
        return;
      }
      tbody.innerHTML = items.map(item => `
        <tr>
          <td><code>${escapeHtml(item.id || "")}</code></td>
          <td>${escapeHtml(item.status || "")}</td>
          <td><code>${escapeHtml(item.command || "")}</code><div class="muted">${escapeHtml(item.cwd || "")}</div></td>
          <td>${escapeHtml(item.reason || item.error || "")}</td>
          <td>${escapeHtml(item.conversation_key || "")}</td>
          <td class="actions">${actions(item)}</td>
        </tr>
      `).join("");
    }
    function actions(item) {
      const id = encodeURIComponent(item.id || "");
      if (item.status === "pending") {
        return `<button onclick="act('${id}','approve')">通过</button><button class="danger" onclick="act('${id}','deny')">拒绝</button><button onclick="act('${id}','revoke')">撤销</button>`;
      }
      return `<button onclick="act('${id}','retry')">重试</button>`;
    }
    async function act(id, action) {
      await api(`/api/approvals/${id}/${action}`, { method: "POST", body: "{}" });
      await refreshAll();
    }
    function renderConfig(config) {
      currentConfig = config || {};
      const modelProfiles = config.model_profiles || {};
      const aiProviders = config.ai_providers || {};
      setModelProfileOptions("cfg-default-profile", modelProfiles, config.default_model_profile || "default");
      setSelectOptions("cfg-provider", providerNames(aiProviders), config.provider || "demo");
      setCustomSelectOptions("cfg-model", modelChoices(config.provider || "demo", modelProfiles), config.model || "");
      document.getElementById("cfg-sandbox-root").value = config.sandbox_root || "";
      document.getElementById("cfg-approval-timeout").value = config.approval_timeout_seconds || 3600;
      document.getElementById("cfg-rag-enabled").value = String(Boolean(config.rag_enabled));
      ensureSelectValue("cfg-rag-backend", config.rag_backend || "pgvector_hybrid_bgem3");
      setCustomSelectOptions("cfg-rag-table", ["echoweave_rag_chunks", "echoweave_rag_documents", "rag_chunks"], config.rag_pgvector_table || "echoweave_rag_chunks");
      document.getElementById("cfg-rag-vector").value = config.rag_vector_weight ?? 0.65;
      document.getElementById("cfg-rag-bm25").value = config.rag_bm25_weight ?? 0.35;
      document.getElementById("cfg-rag-query-rewrite").value = String(Boolean(config.rag_query_rewrite_enabled));
      ensureSelectValue("cfg-rag-query-rewrite-strategy", config.rag_query_rewrite_strategy || "local_multi_query");
      document.getElementById("cfg-rag-query-rewrite-max").value = config.rag_query_rewrite_max_queries || 3;
      document.getElementById("cfg-rag-rerank").value = String(Boolean(config.rag_rerank_enabled));
      ensureSelectValue("cfg-rag-rerank-strategy", config.rag_rerank_strategy || "bm25");
      document.getElementById("cfg-rag-rerank-multiplier").value = config.rag_rerank_candidate_multiplier || 4;
      document.getElementById("cfg-rag-rerank-original").value = config.rag_rerank_original_score_weight ?? 0.65;
      document.getElementById("cfg-rag-rerank-bm25").value = config.rag_rerank_bm25_weight ?? 0.35;
      document.getElementById("cfg-ai-providers").value = JSON.stringify(aiProviders, null, 2);
      document.getElementById("cfg-model-profiles").value = JSON.stringify(modelProfiles, null, 2);
      document.getElementById("cfg-ai-providers-summary").textContent = `${Object.keys(aiProviders).length} 个自定义 provider`;
      const keyCount = Object.values(modelProfiles).filter(profile => profile && profile.api_key_configured).length;
      document.getElementById("cfg-model-profiles-summary").textContent = `${Object.keys(modelProfiles).length} 个模型 profile，${keyCount} 个已保存 API key：${Object.keys(modelProfiles).join(", ") || "未配置"}`;
      document.getElementById("cfg-harness-audit-enabled").value = String(config.harness_audit_enabled !== false);
      document.getElementById("cfg-harness-audit-path").value = config.harness_audit_path || "";
      document.getElementById("cfg-harness-policy").value = JSON.stringify(config.harness_policy || {}, null, 2);
      document.getElementById("cfg-harness-policy-summary").textContent = summarizePolicy(config.harness_policy || {});
      setListStorage("cfg-global-skills", config.global_enabled_skills || []);
      setListStorage("cfg-admins", config.admins || []);
      document.getElementById("cfg-global-skills-summary").textContent = summarizeList(config.global_enabled_skills || [], "未选择全局 skill");
      document.getElementById("cfg-admins-summary").textContent = summarizeList(config.admins || [], "未设置管理员");
    }
    function modelChoices(provider, profiles = {}) {
      const byProvider = {
        demo: ["demo"],
        deepseek: ["deepseek-chat", "deepseek-reasoner"],
        openai: ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini"],
        anthropic: ["claude-3-5-sonnet-latest", "claude-3-7-sonnet-latest"],
        ollama: ["llama3.1", "qwen2.5-coder", "deepseek-r1"],
        openrouter: ["openai/gpt-4.1-mini", "anthropic/claude-3.5-sonnet", "deepseek/deepseek-chat"],
        siliconflow: ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-Coder-32B-Instruct"],
        "openai-compatible": ["local-model", "qwen2.5-coder", "deepseek-chat"],
      };
      const profileModels = Object.values(profiles || {}).map(profile => profile && profile.model).filter(Boolean);
      return Array.from(new Set([...(byProvider[provider] || []), ...profileModels]));
    }
    function apiKeyEnvChoices() {
      return [
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "SILICONFLOW_API_KEY",
        "LOCAL_LLM_API_KEY",
      ];
    }
    function optionList(values, selected) {
      const options = Array.from(new Set([...(values || []), selected].filter(value => value !== undefined && value !== null)));
      return options.map(value => `<option value="${escapeHtml(value)}" ${String(value) === String(selected || "") ? "selected" : ""}>${escapeHtml(value || "不设置")}</option>`).join("");
    }
    function providerNames(aiProviders) {
      const builtins = ["demo", "deepseek", "openai", "anthropic", "ollama", "openrouter", "siliconflow", "openai-compatible"];
      return Array.from(new Set([...builtins, ...Object.keys(aiProviders || {})]));
    }
    function setSelectOptions(id, values, selected) {
      const select = document.getElementById(id);
      const options = Array.from(new Set((values || []).filter(Boolean)));
      if (selected && !options.includes(selected)) options.unshift(selected);
      select.innerHTML = options.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
      if (selected) select.value = selected;
    }
    function setModelProfileOptions(id, profiles, selected) {
      const select = document.getElementById(id);
      const names = Object.keys(profiles || {});
      if (selected && !names.includes(selected)) names.unshift(selected);
      select.innerHTML = names.map(name => {
        const profile = profiles[name] || {};
        const label = profile.label ? `${profile.label} - ${name}` : name;
        const detail = `${profile.provider || "demo"}/${profile.model || "默认模型"}`;
        return `<option value="${escapeHtml(name)}">${escapeHtml(label)} · ${escapeHtml(detail)}</option>`;
      }).join("");
      if (selected) select.value = selected;
    }
    function ensureSelectValue(id, value) {
      const select = document.getElementById(id);
      if (![...select.options].some(option => option.value === value)) {
        select.insertAdjacentHTML("beforeend", `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`);
      }
      select.value = value;
    }
    function setCustomSelectOptions(id, values, selected) {
      const select = document.getElementById(id);
      const custom = document.getElementById(id + "-custom");
      const customWrap = document.getElementById(id + "-custom-wrap");
      const options = Array.from(new Set((values || []).filter(Boolean)));
      const isCustom = selected && !options.includes(selected);
      select.innerHTML = [
        ...options.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`),
        '<option value="__custom__">自定义...</option>'
      ].join("");
      select.value = isCustom ? "__custom__" : (selected || options[0] || "__custom__");
      if (custom) custom.value = isCustom ? selected : "";
      if (customWrap) customWrap.classList.toggle("hidden", select.value !== "__custom__");
    }
    function syncCustomSelect(id) {
      const select = document.getElementById(id);
      const customWrap = document.getElementById(id + "-custom-wrap");
      if (customWrap) customWrap.classList.toggle("hidden", select.value !== "__custom__");
    }
    function refreshModelChoices() {
      setCustomSelectOptions("cfg-model", modelChoices(valueOf("cfg-provider"), parseJsonField("cfg-model-profiles")), customSelectValue("cfg-model"));
    }
    function markCustomSelect(id) {
      document.getElementById(id).value = "__custom__";
    }
    function customSelectValue(id) {
      const select = document.getElementById(id);
      if (!select) return "";
      if (select.value === "__custom__") return valueOf(id + "-custom");
      return select.value;
    }
    function setListStorage(id, values) {
      document.getElementById(id).value = JSON.stringify(Array.from(new Set((values || []).filter(Boolean))), null, 2);
    }
    function getListStorage(id) {
      try {
        const value = JSON.parse(document.getElementById(id).value || "[]");
        return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
      } catch {
        return [];
      }
    }
    function summarizeList(values, emptyText) {
      return values.length ? values.join(", ") : emptyText;
    }
    function summarizePolicy(policy) {
      const deniedTools = (policy.denied_tools || []).length;
      const deniedPaths = (policy.denied_paths || []).length;
      const approvalRules = (policy.command_approval_patterns || []).length;
      const modelLimits = (policy.session_model_allowlist || []).length;
      return `禁用工具 ${deniedTools}，拒绝路径 ${deniedPaths}，命令审批规则 ${approvalRules}，模型限制 ${modelLimits}`;
    }
    function openListPicker(target) {
      listPickerTarget = target;
      const title = target === "admins" ? "选择管理员" : "选择全局 skills";
      const storageId = target === "admins" ? "cfg-admins" : "cfg-global-skills";
      const selected = new Set(getListStorage(storageId));
      const candidates = target === "admins" ? adminCandidates(selected) : skillCandidates(selected);
      document.getElementById("list-modal-title").textContent = title;
      document.getElementById("list-picker").innerHTML = candidates.map(value => `
        <div class="choice-item">
          <label><input type="checkbox" value="${escapeHtml(value)}" ${selected.has(value) ? "checked" : ""}> <strong>${escapeHtml(value)}</strong></label>
        </div>
      `).join("") || '<div class="muted">暂无候选项，可以在下方新增。</div>';
      document.getElementById("list-new-value").value = "";
      document.getElementById("list-modal").classList.remove("hidden");
    }
    function closeListPicker() {
      document.getElementById("list-modal").classList.add("hidden");
      listPickerTarget = null;
    }
    function adminCandidates(selected) {
      return Array.from(new Set(["web-admin", "admin", ...selected, ...(currentConfig.admins || [])])).filter(Boolean);
    }
    function skillCandidates(selected) {
      const skills = (currentCapabilities.skills || []).map(skill => skill && skill.name).filter(Boolean);
      return Array.from(new Set([...skills, ...selected, ...(currentConfig.global_enabled_skills || [])])).filter(Boolean);
    }
    function addListPickerValue() {
      const value = valueOf("list-new-value");
      if (!value) return;
      const existing = [...document.querySelectorAll("#list-picker input")].some(input => input.value === value);
      if (!existing) {
        document.getElementById("list-picker").insertAdjacentHTML("beforeend", `
          <div class="choice-item">
            <label><input type="checkbox" value="${escapeHtml(value)}" checked> <strong>${escapeHtml(value)}</strong></label>
          </div>
        `);
      }
      document.getElementById("list-new-value").value = "";
    }
    function applyListPicker() {
      const values = [...document.querySelectorAll("#list-picker input:checked")].map(input => input.value).filter(Boolean);
      if (listPickerTarget === "admins") {
        setListStorage("cfg-admins", values);
        document.getElementById("cfg-admins-summary").textContent = summarizeList(values, "未设置管理员");
      } else if (listPickerTarget === "global_enabled_skills") {
        setListStorage("cfg-global-skills", values);
        document.getElementById("cfg-global-skills-summary").textContent = summarizeList(values, "未选择全局 skill");
      }
      closeListPicker();
    }
    function parseJsonField(id) {
      return JSON.parse(document.getElementById(id).value || "{}");
    }
    function openJsonEditor(target) {
      editorTarget = target;
      const id = targetToTextarea(target);
      const data = parseJsonField(id);
      document.getElementById("json-modal-title").textContent = editorTitle(target);
      document.getElementById("json-editor").value = JSON.stringify(data, null, 2);
      renderJsonForm(target, data);
      document.getElementById("json-error").textContent = "";
      document.getElementById("json-modal").classList.remove("hidden");
    }
    function closeJsonEditor() {
      document.getElementById("json-modal").classList.add("hidden");
      editorTarget = null;
    }
    function targetToTextarea(target) {
      return {
        ai_providers: "cfg-ai-providers",
        model_profiles: "cfg-model-profiles",
        harness_policy: "cfg-harness-policy"
      }[target];
    }
    function editorTitle(target) {
      return {
        ai_providers: "AI providers 配置",
        model_profiles: "Model profiles 配置",
        harness_policy: "Harness policy 配置"
      }[target] || "配置编辑";
    }
    function renderJsonForm(target, data) {
      if (target === "ai_providers") return renderProviderForm(data);
      if (target === "model_profiles") return renderModelProfileForm(data);
      return renderHarnessPolicyForm(data);
    }
    function renderProviderForm(data) {
      const rows = Object.entries(data || {});
      document.getElementById("json-form").innerHTML = `
        <div class="full hint-line">用于注册 OpenAI-compatible 平台。名称会作为 model profile 的 provider 使用。</div>
        <div id="provider-rows" class="full">${rows.map(([name, item]) => providerRow(name, item)).join("")}</div>
        <button type="button" onclick="addProviderRow()">新增 provider</button>
      `;
    }
    function providerRow(name, item = {}) {
      const baseUrls = [
        "https://api.openai.com/v1",
        "https://api.deepseek.com",
        "https://openrouter.ai/api/v1",
        "https://api.siliconflow.cn/v1",
        "http://127.0.0.1:11434/v1",
        "http://127.0.0.1:1234/v1",
      ];
      return `<div class="form-grid choice-item provider-row">
        <label>名称<input data-field="name" value="${escapeHtml(name)}" oninput="syncFormToJson()"></label>
        <label>类型<select data-field="type" onchange="syncFormToJson()"><option value="openai-compatible">openai-compatible</option></select></label>
        <label>Base URL<select data-field="base_url" onchange="syncFormToJson()">${optionList(baseUrls, item.base_url || "")}</select></label>
        <label>API key env<select data-field="api_key_env" onchange="syncFormToJson()">${optionList(apiKeyEnvChoices(), item.api_key_env || "OPENAI_API_KEY")}</select></label>
        <label>默认模型<select data-field="default_model" onchange="syncFormToJson()">${optionList(modelChoices(name), item.default_model || item.model || "")}</select></label>
        <label>别名，逗号分隔<input data-field="aliases" value="${escapeHtml((item.aliases || []).join(", "))}" oninput="syncFormToJson()"></label>
      </div>`;
    }
    function addProviderRow() {
      document.getElementById("provider-rows").insertAdjacentHTML("beforeend", providerRow("new-provider", { type: "openai-compatible" }));
      syncFormToJson();
    }
    function renderModelProfileForm(data) {
      const rows = Object.entries(data || {});
      document.getElementById("json-form").innerHTML = `
        <div class="full hint-line">用户端模型下拉框来自这里。profile 名称是用户看到和切换的选项。</div>
        <div id="profile-rows" class="full">${rows.map(([name, item]) => modelProfileRow(name, item)).join("")}</div>
        <button type="button" onclick="addModelProfileRow()">新增 profile</button>
      `;
    }
    function modelProfileRow(name, item = {}) {
      const providers = providerNames(parseJsonField("cfg-ai-providers"));
      const models = modelChoices(item.provider || "demo", parseJsonField("cfg-model-profiles"));
      const keyStatus = item.api_key_configured ? "已保存 API key；留空会保留原 key。" : "未保存 API key；也可以只填写 API key env 使用环境变量。";
      return `<div class="form-grid choice-item profile-row">
        <label>Profile 名称<input data-field="name" value="${escapeHtml(name)}" oninput="syncFormToJson()"></label>
        <label>显示名称<input data-field="label" value="${escapeHtml(item.label || "")}" oninput="syncFormToJson()"></label>
        <label>Provider<select data-field="provider" onchange="syncFormToJson()">${providers.map(provider => `<option value="${escapeHtml(provider)}" ${provider === (item.provider || "demo") ? "selected" : ""}>${escapeHtml(provider)}</option>`).join("")}</select></label>
        <label>Model<select data-field="model" onchange="syncFormToJson()">${optionList(models, item.model || "")}</select></label>
        <label>Base URL<input data-field="base_url" value="${escapeHtml(item.base_url || "")}" oninput="syncFormToJson()"></label>
        <label>API key env<select data-field="api_key_env" onchange="syncFormToJson()">${optionList(["", ...apiKeyEnvChoices()], item.api_key_env || "")}</select></label>
        <label>API key<input type="password" data-field="api_key" autocomplete="new-password" placeholder="${item.api_key_configured ? "留空保留已保存 key" : "可直接粘贴 sk-..."}" oninput="syncFormToJson()"></label>
        <label class="check-row"><input type="checkbox" data-field="clear_api_key" onchange="syncFormToJson()"> <span>清除已保存 API key</span></label>
        <input type="hidden" data-field="api_key_configured" value="${item.api_key_configured ? "true" : ""}">
        <div class="full hint-line">${escapeHtml(keyStatus)} 直接填写 API key 会保存到本地配置文件；更推荐生产环境使用环境变量。</div>
        <label class="full">说明<input data-field="description" value="${escapeHtml(item.description || "")}" oninput="syncFormToJson()"></label>
      </div>`;
    }
    function addModelProfileRow() {
      document.getElementById("profile-rows").insertAdjacentHTML("beforeend", modelProfileRow("new-profile", { provider: "demo" }));
      syncFormToJson();
    }
    function renderHarnessPolicyForm(data) {
      const listValue = key => Array.isArray(data?.[key]) ? data[key].join(", ") : "";
      document.getElementById("json-form").innerHTML = `
        <div class="full hint-line">Harness policy 用来约束工具、路径、命令、模型、skill 和 RAG。列表字段用逗号分隔。</div>
        <label>允许工具<input data-field="allowed_tools" value="${escapeHtml(listValue("allowed_tools"))}" oninput="syncFormToJson()"></label>
        <label>禁用工具<input data-field="denied_tools" value="${escapeHtml(listValue("denied_tools"))}" oninput="syncFormToJson()"></label>
        <label>允许路径<input data-field="allowed_paths" value="${escapeHtml(listValue("allowed_paths"))}" oninput="syncFormToJson()"></label>
        <label>拒绝路径<input data-field="denied_paths" value="${escapeHtml(listValue("denied_paths"))}" oninput="syncFormToJson()"></label>
        <label>命令允许正则<input data-field="command_allow_patterns" value="${escapeHtml(listValue("command_allow_patterns"))}" oninput="syncFormToJson()"></label>
        <label>命令审批正则<input data-field="command_approval_patterns" value="${escapeHtml(listValue("command_approval_patterns"))}" oninput="syncFormToJson()"></label>
        <label>命令拒绝正则<input data-field="command_deny_patterns" value="${escapeHtml(listValue("command_deny_patterns"))}" oninput="syncFormToJson()"></label>
        <label>会话可用模型<input data-field="session_model_allowlist" value="${escapeHtml(listValue("session_model_allowlist"))}" oninput="syncFormToJson()"></label>
        <label>会话可用 skill<input data-field="session_skill_allowlist" value="${escapeHtml(listValue("session_skill_allowlist"))}" oninput="syncFormToJson()"></label>
        <label>强制 RAG<select data-field="session_rag_enabled" onchange="syncFormToJson()">
          <option value="">不强制</option><option value="true">强制开启</option><option value="false">强制关闭</option>
        </select></label>
      `;
      const rag = document.querySelector('[data-field="session_rag_enabled"]');
      rag.value = data?.session_rag_enabled === true ? "true" : data?.session_rag_enabled === false ? "false" : "";
    }
    function syncFormToJson() {
      if (!editorTarget || editorSyncing) return;
      editorSyncing = true;
      const data = collectEditorForm();
      document.getElementById("json-editor").value = JSON.stringify(data, null, 2);
      document.getElementById("json-error").textContent = "";
      editorSyncing = false;
    }
    function syncJsonToForm() {
      if (!editorTarget || editorSyncing) return;
      try {
        const data = JSON.parse(document.getElementById("json-editor").value || "{}");
        editorSyncing = true;
        renderJsonForm(editorTarget, data);
        editorSyncing = false;
        document.getElementById("json-error").textContent = "";
      } catch (err) {
        document.getElementById("json-error").textContent = "JSON 暂时无法解析，修正后会同步到上方表单。";
      }
    }
    function collectEditorForm() {
      if (editorTarget === "ai_providers") {
        const result = {};
        for (const row of document.querySelectorAll(".provider-row")) {
          const item = rowData(row);
          if (!item.name) continue;
          result[item.name] = {
            type: item.type || "openai-compatible",
            base_url: item.base_url || undefined,
            api_key_env: item.api_key_env || "OPENAI_API_KEY",
            default_model: item.default_model || "",
            aliases: splitList(item.aliases),
          };
        }
        return result;
      }
      if (editorTarget === "model_profiles") {
        const result = {};
        for (const row of document.querySelectorAll(".profile-row")) {
          const item = rowData(row);
          if (!item.name) continue;
          result[item.name] = {
            provider: item.provider || "demo",
            model: item.model || null,
            ...(item.label ? { label: item.label } : {}),
            ...(item.description ? { description: item.description } : {}),
            ...(item.base_url ? { base_url: item.base_url } : {}),
            ...(item.api_key_env ? { api_key_env: item.api_key_env } : {}),
            ...(item.clear_api_key ? { clear_api_key: true } : {}),
            ...(item.api_key ? { api_key: item.api_key } : {}),
            ...(!item.api_key && !item.clear_api_key && item.api_key_configured === "true" ? { api_key: KEEP_API_KEY } : {}),
          };
        }
        return result;
      }
      const result = {};
      for (const input of document.querySelectorAll("#json-form [data-field]")) {
        const key = input.dataset.field;
        if (key === "session_rag_enabled") {
          result[key] = input.value === "" ? null : input.value === "true";
        } else {
          result[key] = splitList(input.value);
        }
      }
      return result;
    }
    function rowData(row) {
      const data = {};
      for (const input of row.querySelectorAll("[data-field]")) {
        data[input.dataset.field] = input.type === "checkbox" ? input.checked : input.value.trim();
      }
      return data;
    }
    function splitList(value) {
      return String(value || "").split(",").map(v => v.trim()).filter(Boolean);
    }
    function applyJsonEditor() {
      try {
        const data = JSON.parse(document.getElementById("json-editor").value || "{}");
        document.getElementById(targetToTextarea(editorTarget)).value = JSON.stringify(data, null, 2);
        document.getElementById("json-error").textContent = "";
        closeJsonEditor();
      } catch (err) {
        document.getElementById("json-error").textContent = "JSON 格式错误: " + String(err);
      }
    }
    async function saveConfig() {
      document.getElementById("error").textContent = "";
      let modelProfiles = {};
      let aiProviders = {};
      let harnessPolicy = {};
      try { aiProviders = JSON.parse(document.getElementById("cfg-ai-providers").value || "{}"); }
      catch (err) { document.getElementById("error").textContent = "AI providers JSON 格式错误: " + String(err); return; }
      try { modelProfiles = JSON.parse(document.getElementById("cfg-model-profiles").value || "{}"); }
      catch (err) { document.getElementById("error").textContent = "Model profiles JSON 格式错误: " + String(err); return; }
      try { harnessPolicy = JSON.parse(document.getElementById("cfg-harness-policy").value || "{}"); }
      catch (err) { document.getElementById("error").textContent = "Harness policy JSON 格式错误: " + String(err); return; }
      const splitList = value => String(value || "").split(",").map(v => v.trim()).filter(Boolean);
      const payload = {
        default_model_profile: valueOf("cfg-default-profile") || null,
        provider: valueOf("cfg-provider") || "demo",
        model: customSelectValue("cfg-model") || null,
        sandbox_root: valueOf("cfg-sandbox-root") || null,
        approval_timeout_seconds: Number(valueOf("cfg-approval-timeout") || 3600),
        rag_enabled: valueOf("cfg-rag-enabled") === "true",
        rag_backend: valueOf("cfg-rag-backend") || "pgvector_hybrid_bgem3",
        rag_pgvector_table: customSelectValue("cfg-rag-table") || "echoweave_rag_chunks",
        rag_vector_weight: Number(valueOf("cfg-rag-vector") || 0.65),
        rag_bm25_weight: Number(valueOf("cfg-rag-bm25") || 0.35),
        rag_query_rewrite_enabled: valueOf("cfg-rag-query-rewrite") === "true",
        rag_query_rewrite_strategy: valueOf("cfg-rag-query-rewrite-strategy") || "local_multi_query",
        rag_query_rewrite_max_queries: Number(valueOf("cfg-rag-query-rewrite-max") || 3),
        rag_rerank_enabled: valueOf("cfg-rag-rerank") === "true",
        rag_rerank_strategy: valueOf("cfg-rag-rerank-strategy") || "bm25",
        rag_rerank_candidate_multiplier: Number(valueOf("cfg-rag-rerank-multiplier") || 4),
        rag_rerank_original_score_weight: Number(valueOf("cfg-rag-rerank-original") || 0.65),
        rag_rerank_bm25_weight: Number(valueOf("cfg-rag-rerank-bm25") || 0.35),
        ai_providers: aiProviders,
        model_profiles: modelProfiles,
        harness_audit_enabled: valueOf("cfg-harness-audit-enabled") === "true",
        harness_audit_path: valueOf("cfg-harness-audit-path") || null,
        harness_policy: harnessPolicy,
        global_enabled_skills: getListStorage("cfg-global-skills"),
        admins: getListStorage("cfg-admins"),
      };
      await api("/api/config", { method: "POST", body: JSON.stringify(payload) });
      await refreshAll();
    }
    refreshAll();
    setInterval(refreshAll, 5000);
"""


def _user_script() -> str:
    return _common_script() + """
    let capabilitiesTimer = null;

    function addMessage(role, text) {
      const box = document.getElementById("messages");
      const item = document.createElement("div");
      item.className = "msg " + role;
      item.textContent = text;
      box.appendChild(item);
      box.scrollTop = box.scrollHeight;
    }
    function addEventLine(type, payload) {
      const box = document.getElementById("events");
      if (!box) return;
      const item = document.createElement("div");
      item.className = "event";
      const summary = typeof payload === "string" ? payload : JSON.stringify(payload);
      item.textContent = `${new Date().toLocaleTimeString()} ${type}: ${summary}`;
      box.prepend(item);
      while (box.children.length > 40) box.removeChild(box.lastChild);
    }
    function updateStatusFromReply(reply) {
      document.getElementById("active-conversation").textContent = valueOf("chat-conversation") || "web-coding";
      const text = reply?.text || "";
      const modelMatch = text.match(/model:\\s*(.+)/i);
      const ragMatch = text.match(/rag:\\s*(on|off)/i);
      if (modelMatch) document.getElementById("active-model").textContent = modelMatch[1].trim();
      if (ragMatch) document.getElementById("active-rag").textContent = ragMatch[1].trim();
    }
    function capabilityQuery() {
      const params = new URLSearchParams({
        platform: valueOf("chat-platform") || "web-user",
        conversation_id: valueOf("chat-conversation") || "web-coding",
        sender_id: valueOf("chat-sender") || "web-admin",
      });
      return params.toString();
    }
    function refreshCapabilities() {
      clearTimeout(capabilitiesTimer);
      capabilitiesTimer = setTimeout(loadCapabilities, 200);
    }
    async function loadCapabilities() {
      try {
        const data = await api("/api/capabilities?" + capabilityQuery());
        renderModels(data.models || {});
        renderRag(data.rag || {});
        renderSkills(data.skills || []);
      } catch (err) {
        document.getElementById("error").textContent = String(err);
      }
    }
    function renderModels(models) {
      const select = document.getElementById("model-select");
      const current = models.current || "default";
      const profiles = models.profiles || {};
      const names = Object.keys(profiles);
      select.innerHTML = names.map(name => {
        const profile = profiles[name] || {};
        const diagnostics = profile.diagnostics || {};
        const credential = diagnostics.api_key_env
          ? `${diagnostics.api_key_configured ? "已配置" : "未配置"} ${diagnostics.api_key_env}`
          : "无需 API key";
        const label = `${profile.label || name} - ${profile.provider || "demo"}/${profile.model || "default"} · ${credential}`;
        return `<option value="${escapeHtml(name)}">${escapeHtml(label)}</option>`;
      }).join("");
      if (names.includes(current)) select.value = current;
      const profile = profiles[select.value] || {};
      const diagnostics = profile.diagnostics || {};
      const credential = diagnostics.api_key_env
        ? `${diagnostics.api_key_configured ? "API key 已配置" : "API key 未配置：请设置 " + diagnostics.api_key_env}`
        : "无需 API key";
      document.getElementById("model-details").textContent = select.value
        ? `当前选择 ${profile.label || select.value}，provider=${profile.provider || "demo"}，model=${profile.model || "默认"}，${credential}`
        : "还没有配置可选模型 profile。";
      document.getElementById("active-model").textContent = current;
    }
    function renderRag(rag) {
      document.getElementById("rag-toggle").checked = Boolean(rag.enabled);
      document.getElementById("rag-details").textContent =
        `backend=${rag.backend || "unknown"}，pgvector=${rag.pgvector_configured ? "已配置" : "未配置"}，query rewrite=${rag.query_rewrite_enabled ? "on" : "off"}，rerank=${rag.rerank_enabled ? "on" : "off"}`;
      document.getElementById("active-rag").textContent = rag.enabled ? "on" : "off";
    }
    function renderSkills(skills) {
      const box = document.getElementById("skills-list");
      if (!skills.length) {
        box.innerHTML = '<div class="muted">当前工作区没有发现 skill。</div>';
        return;
      }
      box.innerHTML = skills.map(skill => `
        <div class="choice-item">
          <label><input type="checkbox" ${skill.enabled ? "checked" : ""} onchange="toggleSkill('${escapeHtml(skill.name)}', this.checked)"> <strong>${escapeHtml(skill.name)}</strong></label>
          <small>${escapeHtml(skill.description || "")}${skill.global_enabled ? " · 全局启用" : ""}</small>
        </div>
      `).join("");
    }
    async function sendMessage() {
      const input = document.getElementById("chat-input");
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      await runText(text);
    }
    async function quick(text) {
      const command = String(text || "").trim();
      if (!command) return;
      await runText(command);
    }
    async function runAction(text, label) {
      const command = String(text || "").trim();
      if (!command) return;
      document.getElementById("error").textContent = "";
      addMessage("assistant", `操作：${label || command}`);
      try {
        const result = await sendCommand(command);
        addMessage("assistant", result.reply?.text || JSON.stringify(result, null, 2));
        updateStatusFromReply(result.reply);
        await loadCapabilities();
      } catch (err) {
        const message = String(err);
        document.getElementById("error").textContent = message;
        addMessage("assistant", message);
      }
    }
    async function sendCommand(text) {
      const payload = {
        platform: valueOf("chat-platform") || "web-user",
        conversation_id: valueOf("chat-conversation") || "web-coding",
        sender_id: valueOf("chat-sender") || "web-admin",
        text
      };
      return api("/api/command", { method: "POST", body: JSON.stringify(payload) });
    }
    async function runText(text) {
      document.getElementById("error").textContent = "";
      addMessage("user", text);
      try {
        const result = await sendCommand(text);
        addMessage("assistant", result.reply?.text || JSON.stringify(result, null, 2));
        updateStatusFromReply(result.reply);
        await loadCapabilities();
      } catch (err) {
        const message = String(err);
        document.getElementById("error").textContent = message;
        addMessage("assistant", message);
      }
    }
    async function applySelectedModel() {
      const selected = valueOf("model-select");
      if (!selected) return;
      await runAction("/model " + selected, "切换模型为 " + selected);
    }
    async function toggleRag() {
      const enabled = document.getElementById("rag-toggle").checked;
      await runAction(enabled ? "/rag on" : "/rag off", enabled ? "开启当前会话 RAG" : "关闭当前会话 RAG");
    }
    async function toggleSkill(name, enabled) {
      await runAction(`/skill ${enabled ? "on" : "off"} ${name}`, `${enabled ? "启用" : "关闭"} skill ${name}`);
    }
    async function bindWorkspace() {
      const workspace = valueOf("workspace-path");
      if (!workspace) {
        document.getElementById("error").textContent = "请先填写本地路径。";
        return;
      }
      await runAction("/bind " + workspace, "绑定工作区");
    }
    function connectEvents() {
      const state = document.getElementById("stream-state");
      if (!window.EventSource || !state) return;
      const source = new EventSource("/events", { withCredentials: true });
      source.onopen = () => { state.textContent = "已连接"; state.className = "badge"; };
      source.onerror = () => { state.textContent = "连接中断，浏览器会自动重连"; state.className = "badge muted"; };
      for (const name of ["message.inbound", "message.reply", "message.error", "heartbeat"]) {
        source.addEventListener(name, event => {
          try { addEventLine(name, JSON.parse(event.data)); }
          catch { addEventLine(name, event.data); }
        });
      }
    }
    async function hydrateDefaults() {
      try {
        const status = await api("/api/status");
        const admins = status.config?.admins || [];
        if (admins[0] && valueOf("chat-sender") === "web-admin") {
          document.getElementById("chat-sender").value = admins[0];
        }
      } catch (err) {
        document.getElementById("error").textContent = String(err);
      }
      await loadCapabilities();
    }
    document.getElementById("chat-input").addEventListener("keydown", event => {
      if (event.key !== "Enter") return;
      if (event.ctrlKey || event.metaKey) {
        const input = event.currentTarget;
        const start = input.selectionStart;
        const end = input.selectionEnd;
        input.value = input.value.slice(0, start) + "\\n" + input.value.slice(end);
        input.selectionStart = input.selectionEnd = start + 1;
        event.preventDefault();
        return;
      }
      event.preventDefault();
      sendMessage();
    });
    hydrateDefaults();
    connectEvents();
"""
