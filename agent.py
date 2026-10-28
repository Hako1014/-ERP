import json
from llm import request, completed
from business import TOOLS, execute

SYSTEM = '''你是电脑租赁销售助手，始终中文回复。
价格与库存只能依据工具。先查目录，再按需核查所选型号库存和计算费用。
库存不足时比较其他型号，不擅自改变客户数量；缺少数量或租月数就追问。
依据工具返回的数据说明方案。明确主机范围、押金另计、未预留、未保存。
不声称已下单或已出正式报价。用户要求下单时说明本程序没有此权限。
回复按结论、费用、待确认事项组织。'''


def run_turn(history, text, max_steps=8):
    # 在副本上执行，失败不会将不完整协议写入下一轮历史。
    messages = history + [{'role': 'user', 'content': text}]
    for step in range(max_steps):
        response = request(messages, TOOLS)
        message = completed(response)
        calls = message.tool_calls or []
        if not calls:
            if not message.content:
                raise RuntimeError('空回复')
            messages.append(message.model_dump(exclude_none=True))
            return message.content, messages
        if response.choices[0].finish_reason != 'tool_calls':
            raise RuntimeError('工具调用结束状态异常，未执行')
        # 必须先保存assistant工具请求，再逐一配对tool结果。
        messages.append(message.model_dump(exclude_none=True))
        for call in calls:
            result = execute(call.function.name, call.function.arguments)
            print(f'[工具 step={step + 1}] {call.function.name}: '
                  + json.dumps(result, ensure_ascii=False))
            messages.append({'role': 'tool', 'tool_call_id': call.id,
                             'content': json.dumps(result, ensure_ascii=False)})
    raise RuntimeError('达到模型调用上限，本轮未完成')


if __name__ == '__main__':
    history = [{'role': 'system', 'content': SYSTEM}]
    while True:
        text = input('销售> ').strip()
        if text == '/quit':
            break
        if text == '/reset':
            history = [{'role': 'system', 'content': SYSTEM}]
            continue
        if not text:
            continue
        try:
            answer, history = run_turn(history, text)
            print('Agent>', answer)
        except Exception as exc:
            # 不打印SDK完整异常，避免将请求或鉴权信息带入日志。
            print('本轮失败，历史未提交：', type(exc).__name__)