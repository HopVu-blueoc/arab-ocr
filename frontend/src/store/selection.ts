import { create } from "zustand";

interface SelectionState {
  selectedId: number | null;
  hoveredId: number | null;
  select: (id: number | null) => void;
  hover: (id: number | null) => void;
}

export const useSelection = create<SelectionState>((set) => ({
  selectedId: null,
  hoveredId: null,
  select: (id) => set({ selectedId: id }),
  hover: (id) => set({ hoveredId: id }),
}));
