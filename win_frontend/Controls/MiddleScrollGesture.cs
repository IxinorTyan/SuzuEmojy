using System;
using System.Windows;

namespace SuzuEmojy.Windows.Controls;

// Input-independent gesture state; all distances are relative to the viewport, not the scrolling content.
internal sealed class MiddleScrollGesture(double dragWidth, double dragHeight)
{
    private Point origin, last;
    private bool moved;
    private double remainder;

    internal bool Pressed { get; private set; }
    internal bool Active { get; private set; }

    internal void Start(Point position)
    {
        origin = last = position;
        moved = false;
        remainder = 0;
        Pressed = Active = true;
    }

    internal double Drag(Point position)
    {
        if (!Pressed || (!moved && !PastThreshold(position))) return 0;
        moved = true;
        double delta = last.Y - position.Y;
        last = position;
        return delta;
    }

    internal bool Release(Point position)
    {
        if (!Pressed) return false;
        Pressed = false;
        return Active = !moved && !PastThreshold(position);
    }

    internal double Advance(Point position, double elapsedSeconds)
    {
        if (!Active || Pressed) return 0;
        double distance = position.Y - origin.Y;
        double speed = Math.Sign(distance) * Math.Min(2400, Math.Max(0, Math.Abs(distance) - 10) * 12);
        if (speed == 0 || speed * remainder < 0) remainder = 0;
        double movement = speed * RuntimePrimitives.Clamp(elapsedSeconds, 0, .05) + remainder;
        double whole = Math.Truncate(movement);
        remainder = movement - whole;
        return whole;
    }

    internal void Stop()
    {
        Active = Pressed = false;
        remainder = 0;
    }

    private bool PastThreshold(Point position) =>
        Math.Abs(position.X - origin.X) >= dragWidth || Math.Abs(position.Y - origin.Y) >= dragHeight;
}
