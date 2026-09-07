import { render } from "@testing-library/react";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { UsageRecordsSection } from "./UsageRecordsSection";

export function renderUsageRecordsSection(search = "section=usage") {
  const location = memoryLocation({
    path: "/app/settings",
    searchPath: search,
    record: true,
  });
  return {
    ...render(
      <Router hook={location.hook} searchHook={location.searchHook}>
        <UsageRecordsSection />
      </Router>,
    ),
    location,
  };
}
