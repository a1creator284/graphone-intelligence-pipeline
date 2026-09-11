"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { API_BASE_URL } from "@/lib/api";

interface NavItem {
  href: string;
  label: string;
  /** Phase 1 ships the overview only; the rest are reserved routes. */
  placeholder?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Dashboard" },
  { href: "/startups", label: "Startups", placeholder: true },
  { href: "/products", label: "Products", placeholder: true },
  { href: "/research", label: "Research", placeholder: true },
  { href: "/news", label: "News", placeholder: true },
  { href: "/jobs", label: "Jobs", placeholder: true },
  { href: "/entities", label: "Entities", placeholder: true },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <div className="sidebar__mark" aria-hidden="true">
          G1
        </div>
        <div>
          <div className="sidebar__title">GraphOne</div>
          <div className="sidebar__subtitle">Intelligence</div>
        </div>
      </div>

      <nav className="nav" aria-label="Main navigation">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`nav__item${isActive ? " nav__item--active" : ""}`}
              aria-current={isActive ? "page" : undefined}
            >
              <span>{item.label}</span>
              {item.placeholder ? (
                <span className="nav__badge">soon</span>
              ) : null}
            </Link>
          );
        })}
      </nav>

      <div className="sidebar__footer">
        API
        <br />
        {API_BASE_URL}
      </div>
    </aside>
  );
}
