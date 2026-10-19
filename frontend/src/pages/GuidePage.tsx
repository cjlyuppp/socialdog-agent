import { Link, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Icon } from "../components/Icon";
import { Page } from "../components/Layout";
import { Button, Field, Notice, PageHeader, Select } from "../components/ui";

const STEPS = [
  {
    title: "确认数据范围", summary: "先知道手里有什么",
    goal: "记下可用时间、样本定义和金额含义，为后面的比较确定范围。",
    prompt: "我想研究商品调价前后的表现。请先介绍当前数据的覆盖时间、截止日期、基准样本定义、分组披露门槛，以及记录件数和记录收款金额的含义。",
    observe: ["确认可查询的起止日期，后续问题都在这个范围内。", "区分订单数、商品记录件数和记录收款；收款不等于利润。"],
    judgment: "先确定要研究的现象，以及这些数据能否观察到它。",
  },
  {
    title: "找到一次调价", summary: "把大问题缩小到一个事件",
    goal: "选定一个具体商品和调价日期，后续围绕同一个事件追问。",
    prompt: "请查找当前可见历史数据中的持续调价事件，列出商品原名、规格、调价日期及调价前后的价格。如果有降价，请选最早的一次作为接下来研究的事件；没有降价则选最早的调价。没有可识别事件时请明确说明。",
    observe: ["记下商品原名、规格、事件日期和价格变化。", "查看价格事件如何识别，避免把短暂的记录差异直接当成正式调价。"],
    judgment: "这个事件是否值得继续研究？比较窗口内是否还有其他调价？",
  },
  {
    title: "比较前后表现", summary: "用同一口径观察变化",
    goal: "得到调价前后各 14 天的描述性对比，并检查实际覆盖天数。",
    prompt: "沿用刚才选定的商品、规格和调价事件，使用基准样本，查看调价前后各14天的记录件数、记录收款、日均表现、占全店收款的比例及工具给出的变化率。说明窗口日期、数据是否覆盖完整、是否有分组被隐藏，以及同期其他调价。只引用工具已计算的数字。",
    observe: ["区分总量与日均，检查前后窗口是否覆盖完整。", "小样本被隐藏不代表零；只显示一侧时，不能完成前后比较。"],
    judgment: "季节、营业天数、促销等是否可能同时变化？未提供的业务背景要留作待核实。",
  },
  {
    title: "核对与质疑", summary: "让结论经得起复查",
    goal: "逐项核对数字、样本和日期，把观察到的变化与原因解释分开。",
    prompt: "请重新查询刚才选定事件的前后比较，并列出本次使用的商品、规格、事件日期、窗口起止日期、样本口径和关键数字依据。说明哪些是数据直接支持的观察，以及为什么目前还不能把变化归因于这次调价。缺少的证据请明确列出。",
    observe: ["在回答旁展开“查看依据”，核对参数、数字和数据版本。", "在“数据核对”中按相同日期、商品和样本复查；商品级汇总与商品加规格的结果可能不同。", "发现数字、步骤或解释有误，可以在回答旁提交问题反馈。"],
    judgment: "数字正确不代表解释正确。没有成本不能判断利润，前后变化也不能直接证明因果。",
  },
  {
    title: "整理研究想法", summary: "把探索变成可讨论的成果",
    goal: "写出一份有依据的初步发现，连同尚未解决的问题一起提交。",
    prompt: null,
    observe: ["按上方记录框架整理实际查询结果，不确定的内容标注“待确认”。", "去“我的研究”提交研究想法，并关联刚才使用的对话。"],
    judgment: "决定下一步需要补充什么数据，以及用什么方法进一步验证。",
  },
];

const RECORD_FIELDS = [
  ["研究问题", "某商品的一次调价前后，记录件数和记录收款发生了什么变化？"],
  ["用到的数据", "填写数据版本、商品与规格、事件日期、前后窗口、样本口径和隐藏分组情况。"],
  ["初步发现", "引用实际查询的关键数字，说明变化方向；把直接观察与可能解释分开写。"],
  ["下一步想怎么做", "列出仍需核实的促销、季节等因素，以及继续验证需要的数据和方法。"],
];

export function GuidePage() {
  const [params, setParams] = useSearchParams();
  const parsedStep = Number(params.get("step") ?? 1);
  const index = Number.isInteger(parsedStep) && parsedStep >= 1 && parsedStep <= STEPS.length ? parsedStep - 1 : 0;
  const step = STEPS[index];
  const sessionId = params.get("session") ?? "";
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, staleTime: 60_000 });
  const missingSession = !!sessionId && !!sessions.data && !sessions.data.some(s => s.id === sessionId);
  const chatPath = sessionId ? `/chat/${encodeURIComponent(sessionId)}` : "/chat";

  function selectStep(next: number) {
    setParams(previous => { const updated = new URLSearchParams(previous); updated.set("step", String(next + 1)); return updated; });
  }

  return <Page wide>
    <PageHeader title="研究入门" description="跟着一个范例，从提出问题到整理第一份研究想法。可以随时跳过，也可以回来继续。" actions={<Link className="guide-text-link" to={chatPath}>直接开始研究<Icon name="right" size={16} /></Link>} />
    <div className="guide-intro">
      <h2>调价之后，商品卖得更多了吗？</h2>
      <p>用历史交易数据找一次调价，比较前后表现，再写下发现与疑问。助理负责查数和计算，你负责选择问题、核对证据与判断解释。</p>
      <div className="guide-data" role="status"><Icon name="data" size={17} />
        {status.data ? <span>可用数据：{status.data.agent_observation_start} 至 {status.data.agent_observation_end}；截止日期 {status.data.cutoff_date}。每组至少 {status.data.min_orders_per_group} 个订单。</span>
          : status.isError ? <span>暂时无法读取数据范围。<button type="button" className="underline" onClick={() => status.refetch()}>重新读取</button></span>
            : <span>正在读取当前可用的数据范围…</span>}
      </div>
    </div>

    <div className="guide-layout">
      <nav className="guide-steps" aria-label="研究步骤"><ol>{STEPS.map((item, i) => <li key={item.title}>
        <button type="button" aria-current={i === index ? "step" : undefined} aria-controls="guide-step" onClick={() => selectStep(i)}>
          <span className="guide-step-number" aria-hidden="true">{i + 1}</span><span><strong>{item.title}</strong><small>{item.summary}</small></span>
        </button>
      </li>)}</ol></nav>

      <section className="guide-content" id="guide-step" aria-labelledby="guide-step-title">
        <h2 id="guide-step-title">{index + 1}. {step.title}</h2>
        <p className="guide-goal">{step.goal}</p>
        {step.prompt ? <>
          <h3>可以这样问</h3>
          <blockquote className="guide-prompt">{step.prompt}</blockquote>
          <div className="guide-query-actions">
            <Field label="练习使用的对话"><Select value={sessionId} disabled={sessions.isPending || sessions.isError} onChange={event => setParams(previous => { const updated = new URLSearchParams(previous); if (event.target.value) updated.set("session", event.target.value); else updated.delete("session"); return updated; })}>
              {missingSession && <option value={sessionId}>原对话不可用，请重新选择</option>}
              <option value="">新建研究对话</option>
              {sessions.data?.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}
            </Select></Field>
            {sessions.isPending ? <p className="guide-hint" role="status">正在读取对话…</p>
              : sessions.isError ? <Notice>暂时无法读取对话。<button type="button" className="underline" onClick={() => sessions.refetch()}>重新读取</button></Notice>
                : missingSession ? <Notice>原对话暂不可用，请选择其他对话或新建研究对话。</Notice>
                  : <Link className="button-link" to={chatPath} state={{ researchDraft: step.prompt }}>填入研究问题<Icon name="right" size={16} /></Link>}
          </div>
          <p className="guide-hint">仅填入问题，发送前可修改；发送后消耗一次提问额度。后续步骤请选择同一对话，保留已选事件的上下文。</p>
        </> : <>
          <dl className="guide-record">{RECORD_FIELDS.map(([label, description]) => <div key={label}><dt>{label}</dt><dd>{description}</dd></div>)}</dl>
          <details className="guide-alternative"><summary>看看“初步发现”可以怎么写</summary>
            <p>以下仅示范表达，不代表实际查询结论。请用工具结果替换括号内的内容。</p>
            <blockquote className="guide-prompt">在［商品与规格］于［日期］调价前后各 14 天的基准样本中，日均记录件数由［工具数值］变为［工具数值］，记录收款由［工具数值］变为［工具数值］。前后窗口［是否完整］，［是否存在隐藏分组］。这说明所选窗口内出现了［观察到的变化］；促销、季节等因素仍待确认，目前不能将变化归因于调价，也不能据此判断利润。</blockquote>
          </details>
        </>}

        <h3>看什么、做什么</h3>
        <ul className="guide-observe">{step.observe.map(text => <li key={text}>{text}</li>)}</ul>
        <div className="guide-judgment"><h3>留给你的判断</h3><p>{step.judgment}</p></div>
        {index === 1 && <details className="guide-alternative"><summary>没有可识别的调价事件怎么办？</summary><p>改用月度走势练习：先在同一对话中询问“按月汇总基准样本的订单数、记录件数、记录收款和日均，标出不完整月份”，再按相同流程核对和整理发现。不要假设数据中一定有调价。</p></details>}
        <div className="guide-step-actions">
          {index === 3 && <Link className="guide-text-link" to="/reference">打开数据核对<Icon name="right" size={16} /></Link>}
          {index === 4 ? <Link className="button-link" to="/mine">去提交研究想法<Icon name="right" size={16} /></Link> : <Button onClick={() => selectStep(index + 1)}>查看下一步<Icon name="right" size={16} /></Button>}
        </div>
      </section>
    </div>

    <footer className="guide-footer"><p>这个范例提供研究路径，结论以实际查询为准。完成后，你应能说明：观察到了什么、依据是什么、哪些问题还不能回答。</p><p>也可以到<Link to="/evaluate">测评</Link>先独立作答、再对照助理；在回答旁提交问题反馈，帮助改进研究助理。</p></footer>
  </Page>;
}
