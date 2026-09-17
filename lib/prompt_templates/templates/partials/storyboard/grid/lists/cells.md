{% for cell in transitions %}
格{{ cell.index }}（row{{ cell.row }} col{{ cell.col }}）— {{ cell.from_id }}→{{ cell.to_id }}过渡：
  {{ cell.action }}，过渡到 {{ cell.description }}
{% endfor %}
{% for cell in placeholders %}
格{{ cell.index }}（row{{ cell.row }} col{{ cell.col }}）— 空占位：纯灰色背景，无任何内容
{% endfor %}