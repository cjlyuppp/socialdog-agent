import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { Field, Select } from '../components/ui';

function Fixture({ required = false, inDialog = false }: { required?: boolean; inDialog?: boolean }) {
  const [value, setValue] = useState('');
  const content = <form aria-label="选择表单"><Field label="商品" hint="选择一个商品"><Select name="product" required={required} value={value} onValueChange={setValue}><option value="">请选择商品</option><option value="coffee">Coffee</option><option disabled value="unavailable">Unavailable</option><option value="latte">Latte</option></Select></Field><button type="submit">查询</button><output>{value}</output></form>;
  return inDialog ? <dialog open>{content}</dialog> : <div style={{ overflow: 'hidden' }}>{content}</div>;
}

describe('shared selection control', () => {
  it('uses a labelled custom control and commits a pointer selection', () => {
    const view = render(<Fixture />);
    expect(view.container.querySelector('select')).toBeNull();
    fireEvent.click(screen.getByRole('combobox', { name: '商品' }));
    fireEvent.click(screen.getByRole('option', { name: 'Latte' }));
    expect(screen.getByRole('combobox').textContent).toContain('Latte');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(new FormData(screen.getByRole('form') as HTMLFormElement).get('product')).toBe('latte');
  });

  it('skips disabled choices, supports bounds and commits only on Enter', () => {
    render(<Fixture />);
    const trigger = screen.getByRole('combobox'); trigger.focus();
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    expect(trigger.getAttribute('aria-activedescendant')).toBe(screen.getByRole('option', { name: 'Latte' }).id);
    fireEvent.keyDown(trigger, { key: 'Escape' });
    expect(trigger.textContent).toContain('请选择商品');
    fireEvent.keyDown(trigger, { key: 'End' });
    fireEvent.keyDown(trigger, { key: 'Enter' });
    expect(trigger.textContent).toContain('Latte');
    fireEvent.keyDown(trigger, { key: 'Home' });
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    fireEvent.keyDown(trigger, { key: 'Enter' });
    expect(trigger.textContent).toContain('Coffee');
  });

  it('keeps native form validation and focuses the visible control', () => {
    render(<Fixture required />);
    const form = screen.getByRole('form') as HTMLFormElement;
    act(() => { expect(form.checkValidity()).toBe(false); });
    const trigger = screen.getByRole('combobox');
    expect(document.activeElement).toBe(trigger);
    expect(screen.getByRole('alert').textContent).toContain('请选择');
    fireEvent.click(trigger); fireEvent.click(screen.getByRole('option', { name: 'Coffee' }));
    expect(form.checkValidity()).toBe(true);
  });

  it('portals outside clipped containers and stays inside an active dialog', () => {
    const view = render(<Fixture />);
    fireEvent.click(screen.getByRole('combobox'));
    expect(screen.getByRole('listbox').parentElement).toBe(document.body);
    view.unmount();
    render(<Fixture inDialog />); fireEvent.click(screen.getByRole('combobox'));
    expect(screen.getByRole('listbox').closest('dialog')).toBe(screen.getByRole('dialog'));
    fireEvent.click(screen.getByRole('option', { name: 'Coffee' }));
    expect(screen.getByRole('combobox').textContent).toContain('Coffee');
  });

  it('supports type ahead and closes on Tab or outside interaction', () => {
    render(<Fixture />); const trigger = screen.getByRole('combobox'); trigger.focus();
    fireEvent.keyDown(trigger, { key: 'l' }); fireEvent.keyDown(trigger, { key: 'a' });
    expect(trigger.getAttribute('aria-activedescendant')).toBe(screen.getByRole('option', { name: 'Latte' }).id);
    fireEvent.keyDown(trigger, { key: 'Tab' }); expect(screen.queryByRole('listbox')).toBeNull();
    fireEvent.click(trigger); fireEvent.pointerDown(screen.getByRole('button', { name: '查询' }));
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});
