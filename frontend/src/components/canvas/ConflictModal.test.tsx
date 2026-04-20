import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConflictModal } from './ConflictModal';
import { I18nextProvider } from 'react-i18next';
import i18n from '@/i18n';

function renderModal(props: Parameters<typeof ConflictModal>[0]) {
  return render(
    <I18nextProvider i18n={i18n}>
      <ConflictModal {...props} />
    </I18nextProvider>
  );
}

describe('ConflictModal', () => {
  it('renders with existing filename', () => {
    renderModal({
      existing: 'novel.txt',
      suggestedName: 'novel_1',
      onResolve: vi.fn(),
    });
    expect(screen.getByText(/novel\.txt/)).toBeTruthy();
  });

  it('calls onResolve("replace") when Replace clicked', () => {
    const onResolve = vi.fn();
    renderModal({ existing: 'novel.txt', suggestedName: 'novel_1', onResolve });
    fireEvent.click(screen.getByRole('button', { name: /replace|替换/i }));
    expect(onResolve).toHaveBeenCalledWith('replace');
  });

  it('calls onResolve("rename") when Keep both clicked', () => {
    const onResolve = vi.fn();
    renderModal({ existing: 'novel.txt', suggestedName: 'novel_1', onResolve });
    fireEvent.click(screen.getByRole('button', { name: /keep both|保留两者/i }));
    expect(onResolve).toHaveBeenCalledWith('rename');
  });

  it('calls onResolve("cancel") when Cancel clicked', () => {
    const onResolve = vi.fn();
    renderModal({ existing: 'novel.txt', suggestedName: 'novel_1', onResolve });
    fireEvent.click(screen.getByRole('button', { name: /cancel|取消/i }));
    expect(onResolve).toHaveBeenCalledWith('cancel');
  });
});
