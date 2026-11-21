using System;
using System.Threading;

namespace SuzuEmojy.Windows;

internal enum LeavePolicy
{
    CancelOnLeave, // Read-only work whose result can be discarded.
    FinishBeforeLeaving, // Writes that must not be interrupted.
    CancelAndWait // Cooperative cancellation; keep showing the committed result.
}

// A UI-thread-owned work slot. Widgets decide how to present progress; the lease
// owns cancellation, late callback suppression, and the page's leave policy.
internal sealed class OperationScope
{
    private Operation? current;
    internal bool IsBusy => current is not null;
    internal bool BlocksLeave => current is { Policy: not LeavePolicy.CancelOnLeave };
    internal bool WaitsForCancellation => current is { Policy: LeavePolicy.CancelAndWait };
    internal bool CanCancel => current is { Policy: not LeavePolicy.FinishBeforeLeaving, Token.IsCancellationRequested: false };

    internal Operation Begin(LeavePolicy policy, Action? onCancel = null)
    {
        if (IsBusy) throw new InvalidOperationException("An operation is already running in this scope.");
        return current = new(this, policy, onCancel);
    }

    internal bool RequestCancel() => CanCancel && current!.Cancel();

    internal sealed class Operation(OperationScope owner, LeavePolicy policy, Action? onCancel) : IDisposable
    {
        private readonly CancellationTokenSource? cancellation = policy == LeavePolicy.FinishBeforeLeaving ? null : new();
        private bool reporting = true;
        internal LeavePolicy Policy { get; } = policy;
        internal CancellationToken Token => cancellation?.Token ?? CancellationToken.None;
        internal bool AcceptsResult => owner.current == this &&
            (Policy == LeavePolicy.CancelAndWait || !Token.IsCancellationRequested);

        internal IProgress<LibraryProgress> Progress(Action<LibraryProgress> update) =>
            new Progress<LibraryProgress>(value => { if (reporting && AcceptsResult) update(value); });

        internal void StopReporting() => reporting = false;

        internal bool Cancel()
        {
            if (owner.current != this || cancellation is null || cancellation.IsCancellationRequested) return false;
            cancellation.Cancel();
            onCancel?.Invoke();
            return true;
        }

        public void Dispose()
        {
            if (owner.current != this) return;
            owner.current = null;
            cancellation?.Dispose();
        }
    }
}
