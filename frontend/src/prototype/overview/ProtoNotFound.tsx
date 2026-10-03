// PROTOTYPE — 项目内未知路径的兜底（#2981），不合并。三种处理由「项目内未知路径」轴切换。

import { useEffect } from "react";
import { Redirect } from "wouter";
import { useAppStore } from "@/stores/app-store";
import { useOverviewProto } from "./store";
import { NotFoundCanvas } from "./sections";

function RedirectWithToast() {
  useEffect(() => {
    useAppStore.getState().pushToast("这个页面不存在，已回到概览", "info");
  }, []);
  return <Redirect to="/" replace />;
}

export function ProtoNotFound() {
  const { axes } = useOverviewProto();
  if (axes.notFound === "blank") return null;
  if (axes.notFound === "redirect") return <RedirectWithToast />;
  return <NotFoundCanvas />;
}
