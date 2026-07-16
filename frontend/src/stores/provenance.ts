import { create } from "zustand";
import type { ProvenanceSource } from "@/lib/api";

export type ContextSection = "sources" | "research" | "debate";

interface ProvenanceState {
  sources: ProvenanceSource[];
  focusedRefId: string | null;
  expandedRefIds: Set<string>;
  drawerOpen: boolean;
  drawerWide: boolean;
  activeSection: ContextSection;
  setSources: (sources: ProvenanceSource[]) => void;
  upsertSource: (source: ProvenanceSource) => void;
  focusSource: (refId: string) => void;
  toggleExpanded: (refId: string) => void;
  setDrawerOpen: (open: boolean) => void;
  setDrawerWide: (wide: boolean) => void;
  setActiveSection: (section: ContextSection) => void;
  reset: () => void;
}

const initialState = {
  sources: [] as ProvenanceSource[],
  focusedRefId: null as string | null,
  expandedRefIds: new Set<string>(),
  drawerOpen: false,
  drawerWide: false,
  activeSection: "sources" as ContextSection,
};

export const useProvenanceStore = create<ProvenanceState>((set, get) => ({
  ...initialState,
  setSources: (sources) => set({ sources }),
  upsertSource: (source) =>
    set((state) => {
      const idx = state.sources.findIndex((s) => s.ref_id === source.ref_id);
      const next = [...state.sources];
      if (idx >= 0) next[idx] = source;
      else next.push(source);
      return { sources: next };
    }),
  focusSource: (refId) =>
    set({
      focusedRefId: refId,
      drawerOpen: true,
      drawerWide: true,
      activeSection: "sources",
      expandedRefIds: new Set([...get().expandedRefIds, refId]),
    }),
  toggleExpanded: (refId) =>
    set((state) => {
      const next = new Set(state.expandedRefIds);
      if (next.has(refId)) next.delete(refId);
      else next.add(refId);
      return {
        expandedRefIds: next,
        focusedRefId: refId,
        drawerWide: next.size > 0,
      };
    }),
  setDrawerOpen: (open) => set({ drawerOpen: open, drawerWide: open ? get().drawerWide : false }),
  setDrawerWide: (wide) => set({ drawerWide: wide }),
  setActiveSection: (section) => set({ activeSection: section }),
  reset: () => set({ ...initialState, expandedRefIds: new Set() }),
}));

export function preprocessCitationLinks(content: string): string {
  return content.replace(/\[\[([^|\]]+)\|([^\]]+)\]\]/g, "[$2](#source-$1)");
}
