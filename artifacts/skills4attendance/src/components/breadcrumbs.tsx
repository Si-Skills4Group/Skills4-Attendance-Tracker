import * as React from "react";
import { Link } from "wouter";
import { ChevronRight } from "lucide-react";

interface BreadcrumbProps {
  items: {
    label: string;
    href?: string;
  }[];
}

export function Breadcrumbs({ items }: BreadcrumbProps) {
  return (
    <nav className="flex items-center space-x-1.5 text-sm text-muted-foreground mb-6 page-transition-enter">
      {items.map((item, index) => {
        const isLast = index === items.length - 1;
        
        return (
          <div key={item.label} className="flex items-center">
            {item.href && !isLast ? (
              <Link href={item.href}>
                <span className="hover:text-foreground transition-colors cursor-pointer font-medium">{item.label}</span>
              </Link>
            ) : (
              <span className={isLast ? "text-foreground font-semibold" : ""}>{item.label}</span>
            )}
            {!isLast && <ChevronRight className="w-4 h-4 mx-1.5 text-muted-foreground/50 shrink-0" />}
          </div>
        );
      })}
    </nav>
  );
}
