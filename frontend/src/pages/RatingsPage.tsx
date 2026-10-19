import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../api/client";
import type { Rating } from "../api/types";
import { Markdown } from "../components/answer";
import { Page } from "../components/Layout";
import { Badge, Card, Empty, Notice, PageHeader, Spinner } from "../components/ui";
import { dateTime, errorText } from "../lib/format";

const VERDICT_LABELS = { correct: "数字正确", partial: "部分正确", wrong: "数字有误", not_applicable: "不涉及数字" } as const;
const VERDICT_TONES = { correct: "ok", partial: "warn", wrong: "accent", not_applicable: "neutral" } as const;

export function RatingCard({ rating, showUser = false }: { rating: Rating; showUser?: boolean }) {
  const { scores } = rating;
  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center gap-2">
        <strong className="text-base font-semibold">{rating.question_title}</strong>
        <Badge tone={VERDICT_TONES[scores.numbers]}>{VERDICT_LABELS[scores.numbers]}</Badge>
        <span className="ml-auto text-xs text-muted">{showUser && `${rating.username} · `}{dateTime(rating.created_at)}</span>
      </div>
      <p className="mt-3 text-sm leading-7 text-muted">{rating.question}</p>
      <div className="mt-4 flex flex-wrap gap-5 text-sm">
        <span>推理 <strong>{scores.reasoning}</strong>/5</span>
        <span>口径 <strong>{scores.definitions}</strong>/5</span>
        <span>局限性 <strong>{scores.limitations}</strong>/5</span>
      </div>
      {rating.note && <p className="mt-4 rounded-lg bg-sidebar px-4 py-3 text-sm leading-7">{rating.note}</p>}
      <details className="mt-4">
        <summary className="cursor-pointer text-sm text-muted underline">查看答案对照</summary>
        <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2 [&>*]:min-w-0">
          <div><div className="mb-3 text-sm font-medium">我的答案</div><p className="text-[15px] leading-7 whitespace-pre-wrap">{rating.own_answer}</p></div>
          <div className="min-w-0"><div className="mb-3 text-sm font-medium">研究助理的回答</div><Markdown text={rating.agent_answer} /></div>
        </div>
      </details>
    </Card>
  );
}

export function RatingsPage() {
  const ratings = useQuery({ queryKey: ["ratings", "mine"], queryFn: api.myRatings });
  return (
    <Page>
      <PageHeader title="我的测评" actions={<Link to="/evaluate" className="button-link">做一道新题</Link>} />
      {ratings.isPending ? <Spinner /> : ratings.error ? <Notice>{errorText(ratings.error)}</Notice>
        : ratings.data.length === 0 ? <Empty>还没有测评记录。<br /><Link to="/evaluate" className="mt-3 inline-block text-accent underline">开始第一次测评</Link></Empty>
        : <div className="space-y-3">{ratings.data.map((r) => <RatingCard key={r.id} rating={r} />)}</div>}
    </Page>
  );
}
