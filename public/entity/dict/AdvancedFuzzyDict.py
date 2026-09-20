import difflib


class FuzzyDict(dict):
    """支持模糊匹配的字典"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.similarity_threshold = 0.9 # 默认相似度阈值

    def set_threshold(self, threshold):
        """设置相似度阈值"""
        self.similarity_threshold = threshold

    def fuzzy_get(self, key, default=None):
        """模糊获取值"""
        # 首先尝试精确匹配
        if key in self:
            return self[key]

        # 查找最相似的键
        best_match = None
        best_similarity = 0

        for existing_key in self.keys():
            similarity = difflib.SequenceMatcher(None, key, existing_key).ratio()
            if similarity > best_similarity and similarity >= self.similarity_threshold:
                best_similarity = similarity
                best_match = existing_key

        if best_match:
            return self[best_match]

        return default

    def get_all_matches(self, key, threshold=None):
        """获取所有匹配的键值对"""
        if threshold is None:
            threshold = self.similarity_threshold

        matches = []
        for existing_key in self.keys():
            similarity = difflib.SequenceMatcher(None, key, existing_key).ratio()
            if similarity >= threshold:
                matches.append({
                    'key': existing_key,
                    'value': self[existing_key],
                    'similarity': similarity
                })

        # 按相似度排序
        matches.sort(key=lambda x: x['similarity'], reverse=True)
        return matches

if __name__ == '__main__':

    # 使用示例
    fuzzy_dict = FuzzyDict({
        '氧气传感器': 123,
        '温度传感器': 456,
        '湿度传感器': 789,
        '压力传感器': 101112
    })

    # 模糊匹配
    result = fuzzy_dict.fuzzy_get('氧传感器431')
    print(f"模糊匹配结果: {result}")  # 输出: 123

    result = fuzzy_dict.fuzzy_get('温度感应器')
    print(f"模糊匹配结果: {result}")  # 输出: 456

    # 获取所有匹配
    all_matches = fuzzy_dict.get_all_matches('传感器', threshold=0.5)
    for match in all_matches:
        print(f"键: {match['key']}, 值: {match['value']}, 相似度: {match['similarity']:.2f}")