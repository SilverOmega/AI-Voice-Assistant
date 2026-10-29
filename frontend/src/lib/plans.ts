export interface PlanTemplate {
  id: string;
  title: string;
  description: string;
  /** Sent as the first message of the new chat. */
  prompt: string;
}

// Each prompt asks the assistant to interview the user first, so the plan fits them
// instead of being a generic list.
export const PLAN_TEMPLATES: PlanTemplate[] = [
  {
    id: "language",
    title: "Learn a new language",
    description: "Daily practice toward your goal",
    prompt:
      "Help me make a plan to learn a new language. Before writing the plan, ask me which language, " +
      "my current level, why I'm learning it, and how much time I have each day. " +
      "Then give me a week-by-week plan with daily practice.",
  },
  {
    id: "company-news",
    title: "Follow company news",
    description: "Keep up with a company you care about",
    prompt:
      "Help me make a plan to keep up with news about a company. First ask me which company, " +
      "what I care about most (products, finances, leadership, competitors) and how much time I have. " +
      "Then suggest kinds of sources to follow and a simple daily or weekly routine.",
  },
  {
    id: "news",
    title: "Stay on top of the news",
    description: "A news routine that doesn't take over your day",
    prompt:
      "Help me make a plan to stay informed about the news without it taking over my day. " +
      "First ask me which topics and regions I care about and how much time I want to spend. " +
      "Then suggest a routine and a balanced mix of sources.",
  },
  {
    id: "fitness",
    title: "Get fit",
    description: "Workouts that fit your schedule",
    prompt:
      "Help me make a fitness plan. First ask me about my goal, my current activity level, " +
      "any injuries or limits, and how many days a week I can train. Then give me a weekly plan.",
  },
  {
    id: "reading",
    title: "Build a reading habit",
    description: "Read more, a little every day",
    prompt:
      "Help me make a plan to build a reading habit. First ask me what I like to read, " +
      "how much I read now, and when in my day I could fit it in. Then give me a simple plan to stick with.",
  },
  {
    id: "custom",
    title: "Something else",
    description: "Plan any goal step by step",
    prompt:
      "Help me make a plan. First ask me what I want to achieve, by when, and how much time I have. " +
      "Then break it into clear steps with a schedule.",
  },
];
