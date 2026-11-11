import { useCallback, useState } from "react";

export type PetKind = "cat" | "dog" | "bunny" | "none";

export const PETS: { id: PetKind; label: string }[] = [
  { id: "cat", label: "Cat" },
  { id: "dog", label: "Dog" },
  { id: "bunny", label: "Bunny" },
  { id: "none", label: "No pet" },
];

const PET_KEY = "assistant.pet";

/** The pet that naps next to the chat while the assistant is offline. Kept on this device. */
export function usePet() {
  const [pet, setPet] = useState<PetKind>(() => {
    try {
      const saved = localStorage.getItem(PET_KEY);
      return PETS.some((p) => p.id === saved) ? (saved as PetKind) : "cat";
    } catch {
      return "cat";
    }
  });
  const update = useCallback((next: PetKind) => {
    setPet(next);
    try {
      localStorage.setItem(PET_KEY, next);
    } catch {
      /* storage unavailable: the choice just won't persist */
    }
  }, []);
  return [pet, update] as const;
}
