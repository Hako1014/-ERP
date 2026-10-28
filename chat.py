from llm import request, completed

messages = [{"role": "system", "content": "你是租赁销售助手，中文回复。"}]
for text in ["需要4台16GB办公电脑，租6个月。", "刚才说了多少台，租多久？"]:
    messages.append({"role": "user", "content": text})
    response = request(messages)
    answer = completed(response)
    messages.append(answer.model_dump(exclude_none=True))
    print("回复：", answer.content)
    print("结束原因：", response.choices[0].finish_reason)
    print("用量：", response.usage)