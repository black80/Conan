import ReactMarkdown from "react-markdown"

import { cn } from "@/lib/utils"

/** Renders the agent's free-text output (summaries, post-mortems, rationale, chat answers) as markdown instead of literal asterisks/backticks. */
export function MarkdownText({ text, className }: { text: string; className?: string }) {
  return (
    <ReactMarkdown
      components={{
        p: ({ children }) => <p className={cn("text-sm", className)}>{children}</p>,
        strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
        em: ({ children }) => <em className="italic">{children}</em>,
        code: ({ children }) => (
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">{children}</code>
        ),
        ul: ({ children }) => (
          <ul className="list-inside list-disc space-y-1 text-sm">{children}</ul>
        ),
        ol: ({ children }) => (
          <ol className="list-inside list-decimal space-y-1 text-sm">{children}</ol>
        ),
        li: ({ children }) => <li>{children}</li>,
        a: ({ children, href }) => (
          <a href={href} target="_blank" rel="noreferrer" className="underline">
            {children}
          </a>
        ),
      }}
    >
      {text}
    </ReactMarkdown>
  )
}
