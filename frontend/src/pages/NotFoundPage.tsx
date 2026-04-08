import { useLocation } from "wouter";

export function NotFoundPage() {
  const [, navigate] = useLocation();

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-gray-950 px-4 animate-[fadeIn_0.5s_ease-out]">
      <p className="text-[8rem] font-extralight leading-none tracking-tighter text-gray-700">
        404
      </p>
      <p className="mt-4 text-lg text-gray-400">Page not found</p>
      <button
        onClick={() => navigate("/app/projects", { replace: true })}
        className="mt-8 rounded-lg border border-gray-700 px-5 py-2.5 text-sm text-gray-300 transition-colors hover:border-gray-500 hover:text-white"
      >
        Back to home
      </button>
    </div>
  );
}
