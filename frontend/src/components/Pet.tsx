import { useEffect, useId, useRef, useState } from "react";
import { Snorer, type SnoreVoice } from "../audio/snore";
import { PETS, type PetKind } from "../hooks/usePet";

type Kind = Exclude<PetKind, "none">;

const VOICES: Record<Kind, SnoreVoice> = {
  cat: { pitch: 95, flutter: 24, rasp: 0.25, volume: 0.32 },
  dog: { pitch: 58, flutter: 30, rasp: 0.5, volume: 0.4 },
  bunny: { pitch: 140, flutter: 18, rasp: 0.12, volume: 0.25 },
};

interface Props {
  kind: Kind;
  /** The assistant is offline, so the pet naps. */
  asleep: boolean;
  className?: string;
}

/**
 * The pet next to the chat: awake and blinking while the assistant is online, napping while it's
 * offline. Asleep, it snores while the mouse is over it; on phones and with the keyboard, tap or
 * press it to start and stop the snoring.
 */
export function Pet({ kind, asleep, className = "" }: Props) {
  const snorer = useRef<Snorer | null>(null);
  const hovering = useRef(false);
  const [snoring, setSnoring] = useState(false);
  // Restarts the breathing animation so it rises and falls with the snore.
  const [breath, setBreath] = useState(0);
  const name = PETS.find((p) => p.id === kind)!.label.toLowerCase();

  const start = () => {
    (snorer.current ??= new Snorer()).start(VOICES[kind]);
    if (!snoring) {
      setSnoring(true);
      setBreath((b) => b + 1);
    }
  };
  const stop = () => {
    snorer.current?.stop();
    setSnoring(false);
  };

  // Woken up: the assistant came back online.
  useEffect(() => {
    if (asleep) return;
    snorer.current?.stop();
    setSnoring(false);
  }, [asleep]);

  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === "hidden") {
        snorer.current?.stop();
        setSnoring(false);
      }
    };
    document.addEventListener("visibilitychange", onHide);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      snorer.current?.stop();
    };
  }, []);

  if (!asleep) {
    return (
      <div className={className}>
        <div role="img" aria-label={`Your ${name} is awake: the assistant is online`} title="The assistant is online">
          <PetDrawing kind={kind} asleep={false} breathKey={0} />
        </div>
      </div>
    );
  }

  return (
    <div className={className}>
      <button
        type="button"
        aria-pressed={snoring}
        aria-label={`Sleeping ${name}: the assistant is offline. Press to ${snoring ? "stop" : "hear"} the snoring.`}
        title="Zzz… the assistant is offline"
        onPointerEnter={(e) => {
          if (e.pointerType === "touch") return;
          hovering.current = true;
          start();
        }}
        onPointerLeave={(e) => {
          if (e.pointerType === "touch") return;
          hovering.current = false;
          stop();
        }}
        // With the mouse over it, a click only unblocks the sound (browsers need one click first).
        onClick={() => (hovering.current || !snoring ? start() : stop())}
        className="relative block w-full cursor-pointer select-none rounded-2xl outline-none focus-visible:ring-2 focus-visible:ring-sky-300"
      >
        <span aria-hidden className="pointer-events-none absolute -top-[12%] left-[44%] h-[40%] w-[30%]">
          {[
            ["z", "left-0 bottom-0 text-[0.8em]", "0s"],
            ["z", "left-[32%] bottom-[30%] text-[1.05em]", "1.2s"],
            ["Z", "left-[66%] bottom-[62%] text-[1.4em]", "2.4s"],
          ].map(([letter, place, delay]) => (
            <span
              key={delay}
              className={`pet-z absolute font-extrabold ${place} ${snoring ? "text-sky-500" : "text-sky-300 dark:text-sky-400"}`}
              style={{ animationDelay: delay }}
            >
              {letter}
            </span>
          ))}
        </span>
        <PetDrawing kind={kind} asleep breathKey={breath} />
        <span className="block text-center text-xs text-slate-400 dark:text-slate-500">The assistant is offline</span>
      </button>
    </div>
  );
}

const INK = "#334155";
const outline = { stroke: INK, strokeWidth: 2.5, strokeLinejoin: "round", strokeLinecap: "round" } as const;
const BLUSH = "#fda4af";
const NOSE = "#f472b6";

/** A tail drawn as a thick line with an outline. */
function Tube({ d, fill }: { d: string; fill: string }) {
  return (
    <>
      <path d={d} fill="none" stroke={INK} strokeWidth={13} strokeLinecap="round" />
      <path d={d} fill="none" stroke={fill} strokeWidth={8} strokeLinecap="round" />
    </>
  );
}

function Blush({ x, y, gap }: { x: number; y: number; gap: number }) {
  return (
    <g fill={BLUSH} opacity={0.6}>
      <ellipse cx={x - gap} cy={y} rx={5} ry={3} />
      <ellipse cx={x + gap} cy={y} rx={5} ry={3} />
    </g>
  );
}

/** Two eyes at x ± gap: closed arcs while asleep, blinking dots while awake. */
function Eyes({ x, y, gap, asleep }: { x: number; y: number; gap: number; asleep: boolean }) {
  if (asleep) {
    return (
      <path
        d={`M${x - gap - 5} ${y} q5 4 10 0 M${x + gap - 5} ${y} q5 4 10 0`}
        fill="none"
        {...outline}
        strokeWidth={2.2}
      />
    );
  }
  return (
    <g className="pet-blink">
      {[x - gap, x + gap].map((cx) => (
        <g key={cx}>
          <ellipse cx={cx} cy={y + 1} rx={2.9} ry={3.6} fill={INK} />
          <circle cx={cx + 1} cy={y - 0.4} r={1.1} fill="#fff" />
        </g>
      ))}
    </g>
  );
}

/** Eyes, blush, nose and mouth, centered on (x, y). */
function Face({ x, y, asleep }: { x: number; y: number; asleep: boolean }) {
  return (
    <>
      <Eyes x={x} y={y} gap={10} asleep={asleep} />
      <Blush x={x} y={y + 7} gap={14} />
      <path d={`M${x - 2.2} ${y + 4.5} h4.4 l-2.2 2.4 z`} fill={NOSE} />
      <path
        d={`M${x} ${y + 7} q-2 2.6 -4.2 0.8 M${x} ${y + 7} q2 2.6 4.2 0.8`}
        fill="none"
        {...outline}
        strokeWidth={1.5}
      />
    </>
  );
}

function PetDrawing({ kind, asleep, breathKey }: { kind: Kind; asleep: boolean; breathKey: number }) {
  const id = useId();
  return (
    <svg viewBox="0 0 160 112" className="block w-full" aria-hidden>
      <defs>
        <linearGradient id={`${id}-pillow`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#bfdbfe" />
          <stop offset="1" stopColor="#7cb6f5" />
        </linearGradient>
      </defs>
      <ellipse cx={84} cy={107} rx={58} ry={4} fill="#0f172a" opacity={0.07} />
      <ellipse cx={84} cy={96} rx={62} ry={11} fill={`url(#${id}-pillow)`} {...outline} />
      <g key={breathKey} className="pet-breathe">
        {kind === "cat" && <Cat asleep={asleep} />}
        {kind === "dog" && <Dog asleep={asleep} />}
        {kind === "bunny" && <Bunny asleep={asleep} />}
      </g>
    </svg>
  );
}

function Cat({ asleep }: { asleep: boolean }) {
  return (
    <>
      <ellipse cx={94} cy={72} rx={44} ry={24} fill="#fff" {...outline} />
      <Tube d="M134 80 C142 100 104 104 74 98" fill="#fff" />
      <polygon points="26,56 28,30 46,42" fill="#fff" {...outline} />
      <polygon points="30,50 31,36 41,43" fill="#fbcfe8" />
      <ellipse cx={50} cy={66} rx={28} ry={23} fill="#fff" {...outline} />
      {/* nightcap */}
      <path d="M40 44 C46 30 66 26 80 34 C92 40 100 52 104 66 C94 60 86 56 77 54 Z" fill="#60a5fa" {...outline} />
      <Tube d="M38 45 C52 40 66 46 78 55" fill="#eff6ff" />
      <circle cx={105} cy={67} r={6} fill="#fff" {...outline} />
      <Face x={50} y={68} asleep={asleep} />
      <ellipse cx={42} cy={90} rx={8} ry={5} fill="#fff" {...outline} />
      <ellipse cx={60} cy={92} rx={8} ry={5} fill="#fff" {...outline} />
    </>
  );
}

function Dog({ asleep }: { asleep: boolean }) {
  const fur = "#fde7c8";
  return (
    <>
      <ellipse cx={94} cy={72} rx={44} ry={24} fill={fur} {...outline} />
      <ellipse cx={110} cy={64} rx={12} ry={8} fill="#e8b98a" />
      <Tube d="M136 70 C146 64 146 54 140 50" fill={fur} />
      <ellipse cx={50} cy={66} rx={28} ry={23} fill={fur} {...outline} />
      <Eyes x={50} y={62} gap={9} asleep={asleep} />
      <Blush x={50} y={71} gap={17} />
      <ellipse cx={50} cy={76} rx={12} ry={8.5} fill="#fff8ee" {...outline} strokeWidth={2} />
      <ellipse cx={50} cy={72} rx={4.2} ry={3} fill={INK} />
      <path d="M50 75 v2.5 M50 77.5 q-3 3 -6 0.5 M50 77.5 q3 3 6 0.5" fill="none" {...outline} strokeWidth={1.5} />
      {/* floppy ears */}
      <path d="M28 48 C14 52 14 76 24 84 C32 78 34 62 36 50 Z" fill="#c98b56" {...outline} />
      <path d="M72 48 C86 52 86 76 76 84 C68 78 66 62 64 50 Z" fill="#c98b56" {...outline} />
      <ellipse cx={42} cy={90} rx={8} ry={5} fill={fur} {...outline} />
      <ellipse cx={60} cy={92} rx={8} ry={5} fill={fur} {...outline} />
    </>
  );
}

function Bunny({ asleep }: { asleep: boolean }) {
  const fur = "#f8fafc";
  return (
    <>
      <ellipse cx={94} cy={74} rx={40} ry={22} fill={fur} {...outline} />
      <circle cx={134} cy={68} r={8} fill="#fff" {...outline} />
      {/* long ears lying back along the body */}
      <ellipse cx={78} cy={60} rx={28} ry={8} transform="rotate(14 78 60)" fill={fur} {...outline} />
      <ellipse cx={80} cy={52} rx={30} ry={8.5} transform="rotate(6 80 52)" fill={fur} {...outline} />
      <ellipse cx={86} cy={52} rx={20} ry={3.8} transform="rotate(6 86 52)" fill="#fbcfe8" />
      <ellipse cx={52} cy={70} rx={25} ry={21} fill={fur} {...outline} />
      <Face x={52} y={70} asleep={asleep} />
      <ellipse cx={44} cy={91} rx={7} ry={4.5} fill={fur} {...outline} />
      <ellipse cx={60} cy={92} rx={7} ry={4.5} fill={fur} {...outline} />
    </>
  );
}
