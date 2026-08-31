import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { catalogDisplayNames, type CatalogDisplayNames } from "@/utils/provider-models";
import type { ModelCandidatesResponse, SystemConfigOptions } from "@/types/system";
import type { CustomProviderInfo } from "@/types/custom-provider";
import type { ProviderInfo } from "@/types/provider";

/** 一份目录译名快照连同它成文所用的语言——语言对不上的快照一律不参与叠加。 */
interface LocalizedSnapshot {
  language: string;
  names: CatalogDisplayNames;
}

/**
 * 下拉里显示的供应商名与模型名，四层叠加、由旧到新：目录（`/providers`，含未 ready 的供应商，
 * 兜住已失去凭证的内置供应商与被停用的自定义模型这类仍是生效值的条目）→ 语言切换后重取的
 * 内置目录 → options（整页配置，随保存重取）→ 候选（随语言重取）。
 *
 * 语言切换不重取整页配置——那会连带清空表单里未保存的编辑。候选随语言重取，但它只列 ready 的
 * 供应商；生效值指向一个已失去凭证的供应商时，名字只有目录这一层给得出，而调用方的目录快照是
 * 随表单一同拉来的、不跟着语言走。故本 hook 自己重取一份目录译名补在候选之下：只取译名，不回写
 * 调用方的目录——能力字段与语言无关，重取它没有收益，还会牵动依赖它的下游计算。
 *
 * 自取的只有内置目录：自定义供应商与模型的名字是用户自填的，不随语言变化，最底层那份即为终值。
 *
 * 挂载语言下不取，也不叠加自取的快照：调用方的目录就是那个语言的，重复请求没有新信息。快照记着
 * 自己成文的语言，切回挂载语言时它自然出局，不会把上一门语言的名字盖在调用方的目录上。
 */
export function useDisplayNames(
  providers: ProviderInfo[],
  customProviders: CustomProviderInfo[],
  options: Pick<SystemConfigOptions, "provider_names" | "model_names"> | null,
  candidates: ModelCandidatesResponse | null,
): CatalogDisplayNames {
  const { i18n } = useTranslation();
  const language = i18n.language;
  const [snapshot, setSnapshot] = useState<LocalizedSnapshot | null>(null);
  const mountLanguageRef = useRef(language);

  useEffect(() => {
    if (language === mountLanguageRef.current) return;
    const controller = new AbortController();
    void API.getProviders({ signal: controller.signal }).then(
      (res) => {
        if (controller.signal.aborted) return;
         
        setSnapshot({ language, names: catalogDisplayNames(res.providers) });
      },
      // 失败保留上一份快照：名字停留在旧语言，好过退回裸 provider id。
      () => undefined,
    );
    return () => controller.abort();
  }, [language]);

  const catalog = useMemo(
    () => catalogDisplayNames(providers, customProviders),
    [providers, customProviders],
  );
  const localized = snapshot?.language === language ? snapshot.names : null;
  return useMemo(
    () => ({
      providerNames: {
        ...catalog.providerNames,
        ...(localized?.providerNames ?? {}),
        ...(options?.provider_names ?? {}),
        ...(candidates?.provider_names ?? {}),
      },
      modelNames: {
        ...catalog.modelNames,
        ...(localized?.modelNames ?? {}),
        ...(options?.model_names ?? {}),
        ...(candidates?.model_names ?? {}),
      },
    }),
    [catalog, localized, options, candidates],
  );
}
