using System;

namespace SuzuEmojy.Windows.Desktop;

internal readonly record struct WindowIdentity(uint Thread, uint Process);
internal readonly record struct PasteTarget(nint Handle, WindowIdentity Identity, nint FocusedControl);

internal interface IPastePlatform
{
    nint Foreground { get; }
    uint ClipboardSequence { get; }
    bool ModifiersDown { get; }
    uint OwnProcess { get; }
    WindowIdentity? Identify(nint window);
    bool IsPasteTarget(nint window);
    nint FocusedControl(PasteTarget target);
    void RestoreForeground(nint window);
    bool SendPaste();
}

internal enum PasteStatus { Idle, Waiting, Pasted, Skipped }

// Owns one bounded paste request; its caller supplies scheduling and presentation.
internal sealed class AutoPaste(IPastePlatform platform, TimeProvider clock)
{
    private static readonly TimeSpan Settle = TimeSpan.FromMilliseconds(100);
    private static readonly TimeSpan Deadline = TimeSpan.FromMilliseconds(300);
    private readonly record struct Request(PasteTarget Target, nint Source, uint Sequence, long Started);
    private Request? pending;

    internal PasteTarget? CaptureForeground()
    {
        var handle = platform.Foreground;
        var identity = platform.Identify(handle);
        if (identity is not { } owner || owner.Process == platform.OwnProcess || !platform.IsPasteTarget(handle))
            return null;
        var target = new PasteTarget(handle, owner, 0);
        var focused = platform.FocusedControl(target);
        return platform.Foreground == handle ? target with { FocusedControl = focused } : null;
    }

    internal bool Begin(PasteTarget target, nint source, uint sequence)
    {
        Cancel();
        var request = new Request(target, source, sequence, clock.GetTimestamp());
        if (!IsCurrent(request) || target.Identity.Process == platform.OwnProcess ||
            platform.Identify(source)?.Process != platform.OwnProcess) return false;

        // Never take focus back if the user has already switched to a different app.
        var foreground = platform.Foreground;
        if (foreground != target.Handle)
        {
            if (foreground != source || platform.Foreground != source) return false;
            platform.RestoreForeground(target.Handle);
        }

        // Activation may complete after the source panel hides. Wait without sending or
        // forcing focus again; passing through an unrelated foreground cancels the handoff.
        if (!IsHandoffWindow(platform.Foreground, request)) return false;
        pending = request;
        return true;
    }

    internal void Cancel() => pending = null;

    internal PasteStatus Tick()
    {
        if (pending is not { } request) return PasteStatus.Idle;
        var elapsed = clock.GetElapsedTime(request.Started);
        var foreground = platform.Foreground;
        if (elapsed >= Deadline || !IsCurrent(request) || !IsHandoffWindow(foreground, request))
            return Finish(PasteStatus.Skipped);
        if (foreground != request.Target.Handle || elapsed < Settle || platform.ModifiersDown) return PasteStatus.Waiting;
        if (!IsExpectedControl(request.Target)) return Finish(PasteStatus.Skipped);

        // These are deliberately sampled again right before the one input batch.
        if (platform.ModifiersDown) return PasteStatus.Waiting;
        if (clock.GetElapsedTime(request.Started) >= Deadline || !IsCurrent(request) || !IsExpectedControl(request.Target) ||
            platform.Foreground != request.Target.Handle)
            return Finish(PasteStatus.Skipped);

        Cancel();
        return platform.SendPaste() ? PasteStatus.Pasted : PasteStatus.Skipped;
    }

    private static bool IsHandoffWindow(nint window, Request request) =>
        window == 0 || window == request.Source || window == request.Target.Handle;

    private bool IsExpectedControl(PasteTarget target) =>
        target.FocusedControl == 0 || target.FocusedControl == platform.FocusedControl(target);

    private bool IsCurrent(Request request) => request.Sequence != 0 &&
        platform.Identify(request.Target.Handle) == request.Target.Identity &&
        platform.IsPasteTarget(request.Target.Handle) &&
        platform.ClipboardSequence == request.Sequence;

    private PasteStatus Finish(PasteStatus result)
    {
        Cancel();
        return result;
    }
}
