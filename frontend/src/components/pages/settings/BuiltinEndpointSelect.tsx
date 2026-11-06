import { useTranslation } from "react-i18next";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { BuiltinEndpointOption } from "@/types";

interface BuiltinEndpointSelectProps {
  /** 触发按钮的 id，供字段标签用 `htmlFor` 关联。 */
  id?: string;
  value: string;
  onChange: (next: string) => void;
  /** 这个供应商在该媒体类型下的内置调用端点，由服务端给出。 */
  options: BuiltinEndpointOption[];
}

/** 内置供应商下用户添加的模型的「调用端点」选择器：只列这个供应商在该媒体类型下的内置调用端点。 */
export function BuiltinEndpointSelect({ id, value, onChange, options }: BuiltinEndpointSelectProps) {
  const { t } = useTranslation("dashboard");
  const label = (option: BuiltinEndpointOption) =>
    option.display_name_key ? t(option.display_name_key) : (option.display_name ?? option.key);
  const selected = options.find((option) => option.key === value);

  return (
    <Select<string>
      value={value}
      onValueChange={(next) => {
        if (next !== null && next !== value) onChange(next);
      }}
      disabled={options.length < 2}
    >
      <SelectTrigger id={id} className="w-full">
        <SelectValue>
          {() => <span className="truncate">{selected ? label(selected) : value}</span>}
        </SelectValue>
      </SelectTrigger>
      <SelectContent alignItemWithTrigger={false}>
        {options.map((option) => (
          <SelectItem key={option.key} value={option.key}>
            <span className="truncate">{label(option)}</span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
