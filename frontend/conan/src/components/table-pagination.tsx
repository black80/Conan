import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination"
import { cn } from "@/lib/utils"

export function TablePagination({
  page,
  pageCount,
  onPageChange,
}: {
  page: number
  pageCount: number
  onPageChange: (page: number) => void
}) {
  if (pageCount <= 1) return null

  const atStart = page <= 1
  const atEnd = page >= pageCount

  return (
    <Pagination className="justify-between pt-2">
      <PaginationContent>
        <PaginationItem>
          <PaginationPrevious
            href="#"
            aria-disabled={atStart}
            className={cn(atStart && "pointer-events-none opacity-50")}
            onClick={(e) => {
              e.preventDefault()
              if (!atStart) onPageChange(page - 1)
            }}
          />
        </PaginationItem>
      </PaginationContent>
      <span className="text-sm text-muted-foreground">
        Page {page} of {pageCount}
      </span>
      <PaginationContent>
        <PaginationItem>
          <PaginationNext
            href="#"
            aria-disabled={atEnd}
            className={cn(atEnd && "pointer-events-none opacity-50")}
            onClick={(e) => {
              e.preventDefault()
              if (!atEnd) onPageChange(page + 1)
            }}
          />
        </PaginationItem>
      </PaginationContent>
    </Pagination>
  )
}
