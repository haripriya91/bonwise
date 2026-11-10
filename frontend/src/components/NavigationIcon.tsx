type Name = "home" | "purchases" | "planner" | "offers" | "analysis" | "household";

// Original inline SVGs: no icon package, external requests or image downloads.
export function NavigationIcon({ name }: { name: Name }) {
  const paths: Record<Name, string> = {
    home: "m3 10 9-7 9 7M5 9v12h14V9M9 21v-8h6v8",
    purchases: "M6 3h12v18l-3-2-3 2-3-2-3 2V3ZM9 7h6M9 11h6M9 15h3",
    planner: "M3 7h18l-2 13H5L3 7Zm5 0 4-5 4 5M9 11v5M15 11v5",
    offers: "m12 2 2.8 6.2L21 11l-6.2 2.8L12 20l-2.8-6.2L3 11l6.2-2.8L12 2ZM20 17v5M17.5 19.5h5",
    analysis: "M4 20V10M12 20V4M20 20v-7",
    household: "M3 21v-7l9-7 9 7v7H3ZM9 21v-6h6v6M8 5a4 4 0 0 1 8 0",
  };
  return <svg className="navigation-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d={paths[name]} /></svg>;
}
