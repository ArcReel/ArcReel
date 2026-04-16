import { create } from "zustand";
import type { SessionMeta, Turn, PendingQuestion, PendingApproval, SkillInfo, SessionStatus } from "@/types";

interface AssistantState {
  // Sessions
  sessions: SessionMeta[];
  currentSessionId: string | null;
  sessionsLoading: boolean;

  // Messages
  turns: Turn[];
  draftTurn: Turn | null;
  messagesLoading: boolean;

  // Input
  input: string;
  sending: boolean;
  interrupting: boolean;
  error: string | null;

  // Session status
  sessionStatus: SessionStatus | null;
  sessionStatusDetail: string | null;

  // Questions
  pendingQuestion: PendingQuestion | null;
  answeringQuestion: boolean;

  // Approvals
  pendingApproval: PendingApproval | null;
  decidingApproval: boolean;

  // Skills
  skills: SkillInfo[];
  skillsLoading: boolean;

  // Scope
  currentProject: string | null;

  // Draft session (lazy creation)
  isDraftSession: boolean;

  // Actions (basic setters -- full logic migrated later)
  setSessions: (sessions: SessionMeta[]) => void;
  setCurrentSessionId: (id: string | null) => void;
  setSessionsLoading: (loading: boolean) => void;
  setTurns: (turns: Turn[]) => void;
  setDraftTurn: (turn: Turn | null) => void;
  setMessagesLoading: (loading: boolean) => void;
  setInput: (input: string) => void;
  setSending: (sending: boolean) => void;
  setInterrupting: (interrupting: boolean) => void;
  setError: (error: string | null) => void;
  setSessionStatus: (status: SessionStatus | null) => void;
  setSessionStatusDetail: (detail: string | null) => void;
  setPendingQuestion: (question: PendingQuestion | null) => void;
  setAnsweringQuestion: (answering: boolean) => void;
  setPendingApproval: (approval: PendingApproval | null) => void;
  setDecidingApproval: (deciding: boolean) => void;
  setSkills: (skills: SkillInfo[]) => void;
  setSkillsLoading: (loading: boolean) => void;
  setCurrentProject: (project: string | null) => void;
  setIsDraftSession: (draft: boolean) => void;
}

export const useAssistantStore = create<AssistantState>((set) => ({
  sessions: [],
  currentSessionId: null,
  sessionsLoading: false,
  turns: [],
  draftTurn: null,
  messagesLoading: false,
  input: "",
  sending: false,
  interrupting: false,
  error: null,
  sessionStatus: null,
  sessionStatusDetail: null,
  pendingQuestion: null,
  answeringQuestion: false,
  pendingApproval: null,
  decidingApproval: false,
  skills: [],
  skillsLoading: false,
  currentProject: null,
  isDraftSession: false,

  setSessions: (sessions) => set({ sessions }),
  setCurrentSessionId: (id) => set({ currentSessionId: id }),
  setSessionsLoading: (loading) => set({ sessionsLoading: loading }),
  setTurns: (turns) => set({ turns }),
  setDraftTurn: (turn) => set({ draftTurn: turn }),
  setMessagesLoading: (loading) => set({ messagesLoading: loading }),
  setInput: (input) => set({ input }),
  setSending: (sending) => set({ sending }),
  setInterrupting: (interrupting) => set({ interrupting }),
  setError: (error) => set({ error }),
  setSessionStatus: (status) => set({ sessionStatus: status }),
  setSessionStatusDetail: (detail) => set({ sessionStatusDetail: detail }),
  setPendingQuestion: (question) => set({ pendingQuestion: question }),
  setAnsweringQuestion: (answering) => set({ answeringQuestion: answering }),
  setPendingApproval: (approval) => set({ pendingApproval: approval }),
  setDecidingApproval: (deciding) => set({ decidingApproval: deciding }),
  setSkills: (skills) => set({ skills }),
  setSkillsLoading: (loading) => set({ skillsLoading: loading }),
  setCurrentProject: (project) => set({ currentProject: project }),
  setIsDraftSession: (draft) => set({ isDraftSession: draft }),
}));
