import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useNavigate } from 'react-router';
import { http, HttpResponse } from 'msw';
import App from '../App';
import { Markdown } from '../components/answer';
import { api, setCsrfToken } from '../api/client';
import { db } from '../mocks/db';
import { server } from './setup';

function NextAttempt({ id }: { id: string }) {
  const navigate = useNavigate();
  return <button onClick={() => navigate(`/evaluate/${id}`)}>下一份测评</button>;
}
function open(path: string, nextAttempt?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { client, ...render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>{nextAttempt && <NextAttempt id={nextAttempt} />}<App /></MemoryRouter></QueryClientProvider>) };
}
async function login(name = 'lin') {
  const me = await api.login(name, 'demo');
  setCsrfToken(me.csrf_token);
  await api.acceptNotice();
}

describe('release review regressions', () => {
  it('returns to the selected question after authentication', async () => {
    open('/evaluate?q=q-week');
    fireEvent.change(await screen.findByLabelText('用户名'), { target: { value: 'lin' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'demo' } });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));
    fireEvent.click(await screen.findByRole('button', { name: '了解并进入工作台' }));
    await screen.findByLabelText('我的答案');
    expect(screen.getByRole('heading', { name: /按周列出/ })).toBeTruthy();
  });
  it('does not carry scores or notes into another cached evaluation', async () => {
    await login();
    const first = await api.runEval('q-month', '第一份独立答案');
    db.jobs.find(j => j.id === first.job_id)!.started_ms -= 60_000;
    await api.job(first.job_id);
    const second = await api.runEval('q-week', '第二份独立答案');
    db.jobs.find(j => j.id === second.job_id)!.started_ms -= 60_000;
    await api.job(second.job_id);
    const view = open(`/evaluate/${first.id}`, second.id);
    fireEvent.click(await screen.findByRole('radio', { name: '全部正确' }));
    fireEvent.change(screen.getByLabelText('备注（可选）'), { target: { value: '只属于第一题的备注' } });
    view.client.setQueryData(['attempt', second.id], await api.attempt(second.id));
    fireEvent.click(screen.getByRole('button', { name: '下一份测评' }));
    await screen.findByText('第二份独立答案');
    expect((screen.getByLabelText('备注（可选）') as HTMLTextAreaElement).value).toBe('');
    expect((screen.getByRole('radio', { name: '全部正确' }) as HTMLInputElement).checked).toBe(false);
  });
  it('unblocks a new conversation when a task on another page completes', async () => {
    await login();
    const session = await api.createSession();
    const { job } = await api.ask(session.id, '数据范围');
    open('/chat');
    fireEvent.change(await screen.findByRole('textbox', { name: '输入研究问题' }), { target: { value: '下一个问题' } });
    await waitFor(() => expect((screen.getByRole('button', { name: '发送问题' }) as HTMLButtonElement).disabled).toBe(true));
    db.jobs.find(j => j.id === job.id)!.started_ms -= 60_000;
    await api.job(job.id);
    await waitFor(() => expect((screen.getByRole('button', { name: '发送问题' }) as HTMLButtonElement).disabled).toBe(false), { timeout: 3500 });
  });

  it('never loads external images or links from untrusted model markdown', () => {
    const { container } = render(<Markdown text={'![图示](https://example.invalid/pixel?data=private)\n\n[外部链接](https://example.invalid)\n\n<img src="https://example.invalid/raw" />'} />);
    expect(container.querySelector('img, a, iframe')).toBeNull();
    expect(screen.getByText('图示')).toBeTruthy();
  });

  it('allows an administrator to replace an unreadable credential configuration', async () => {
    await login('carrey');
    server.use(http.get('/api/admin/model-settings', () => HttpResponse.json({ error: '模型服务配置无法读取，请重新保存。' }, { status: 503 })));
    open('/admin?tab=model');
    await screen.findByText('模型服务配置无法读取，请重新保存。');
    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'integration-only-recovery-credential' } });
    fireEvent.click(screen.getByRole('button', { name: '验证并保存' }));
    await screen.findByText('已保存，新的任务将使用此 API Key。');
    expect((screen.getByLabelText('替换 API Key') as HTMLInputElement).value).toBe('');
  });

  it('exports text as literal CSV cells while retaining negative numeric values', async () => {
    await login();
    const result = await api.reference({ scope: 'baseline', by: 'product', start_date: null, end_date: null, product: null });
    result.rows = [{ ...result.rows[0], group: '=HYPERLINK("https://example.invalid")', recorded_receipt_yuan: -10 }];
    server.use(http.get('/api/reference/query', () => HttpResponse.json(result)));
    const previousCreate = URL.createObjectURL, previousRevoke = URL.revokeObjectURL;
    let exported: Blob | undefined;
    URL.createObjectURL = vi.fn(value => { exported = value as Blob; return 'blob:integration-csv'; });
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    try {
      open('/reference');
      fireEvent.click(await screen.findByRole('button', { name: '导出 CSV' }));
      const text = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(exported!);
      });
      expect(text).toContain('"\'=HYPERLINK(""https://example.invalid"")"');
      expect(text).toContain('"-10"');
    } finally {
      URL.createObjectURL = previousCreate; URL.revokeObjectURL = previousRevoke; click.mockRestore();
    }
  });
});
