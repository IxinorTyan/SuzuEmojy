#if NETFRAMEWORK
namespace System.Runtime.CompilerServices;

// Compiler marker for records and init-only setters; no runtime behavior.
internal static class IsExternalInit { }
#endif
