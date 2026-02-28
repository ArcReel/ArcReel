"""
状态和统计字段的实时计算器

提供读时计算的统计字段，避免存储冗余数据。
配合 ProjectManager 使用，在 API 响应时注入计算字段。
"""

from pathlib import Path
from typing import Dict, List, Any, Tuple


class StatusCalculator:
    """状态和统计字段的实时计算器"""

    def __init__(self, project_manager):
        """
        初始化状态计算器

        Args:
            project_manager: ProjectManager 实例
        """
        self.pm = project_manager

    @classmethod
    def _select_content_mode_and_items(cls, script: Dict) -> Tuple[str, List[Dict]]:
        content_mode = script.get('content_mode')
        if content_mode in {'narration', 'drama'}:
            if content_mode == 'narration' and isinstance(script.get('segments'), list):
                return 'narration', script.get('segments', [])
            if content_mode == 'drama' and isinstance(script.get('scenes'), list):
                return 'drama', script.get('scenes', [])

        if isinstance(script.get('segments'), list):
            return 'narration', script.get('segments', [])
        if isinstance(script.get('scenes'), list):
            return 'drama', script.get('scenes', [])

        return ('narration' if content_mode not in {'narration', 'drama'} else content_mode), []

    def calculate_episode_stats(self, project_name: str, script: Dict) -> Dict:
        """
        计算单个剧集的统计信息

        Args:
            project_name: 项目名称
            script: 剧本数据

        Returns:
            统计信息字典
        """
        content_mode, items = self._select_content_mode_and_items(script)
        default_duration = 4 if content_mode == 'narration' else 8

        # 统计资源完成情况
        storyboard_done = sum(
            1 for i in items
            if i.get('generated_assets', {}).get('storyboard_image')
        )
        video_done = sum(
            1 for i in items
            if i.get('generated_assets', {}).get('video_clip')
        )
        total = len(items)

        # 计算状态
        if video_done == total and total > 0:
            status = 'completed'
        elif storyboard_done > 0 or video_done > 0:
            status = 'in_production'
        else:
            status = 'draft'

        return {
            'scenes_count': total,
            'status': status,
            'duration_seconds': sum(i.get('duration_seconds', default_duration) for i in items),
            'storyboards_completed': storyboard_done,
            'videos_completed': video_done
        }

    def calculate_project_progress(self, project_name: str) -> Dict:
        """
        计算项目整体进度（实时）

        Args:
            project_name: 项目名称

        Returns:
            进度统计字典
        """
        project = self.pm.load_project(project_name)
        project_dir = self.pm.get_project_path(project_name)

        # 人物统计
        chars = project.get('characters', {})
        chars_total = len(chars)
        chars_done = sum(
            1 for c in chars.values()
            if c.get('character_sheet') and (project_dir / c['character_sheet']).exists()
        )

        # 线索统计
        clues = project.get('clues', {})
        clues_total = len([c for c in clues.values() if c.get('importance') == 'major'])
        clues_done = sum(
            1 for c in clues.values()
            if c.get('clue_sheet') and (project_dir / c['clue_sheet']).exists()
        )

        # 分镜/视频统计（遍历所有剧本）
        sb_total, sb_done, vid_total, vid_done = 0, 0, 0, 0

        for ep in project.get('episodes', []):
            script_file = ep.get('script_file', '')
            if script_file:
                try:
                    script = self.pm.load_script(project_name, script_file)
                    stats = self.calculate_episode_stats(project_name, script)
                    sb_total += stats['scenes_count']
                    vid_total += stats['scenes_count']
                    sb_done += stats['storyboards_completed']
                    vid_done += stats['videos_completed']
                except FileNotFoundError:
                    pass

        return {
            'characters': {'total': chars_total, 'completed': chars_done},
            'clues': {'total': clues_total, 'completed': clues_done},
            'storyboards': {'total': sb_total, 'completed': sb_done},
            'videos': {'total': vid_total, 'completed': vid_done}
        }

    def calculate_current_phase(self, progress: Dict) -> str:
        """
        根据进度推断当前阶段

        Args:
            progress: 进度统计字典

        Returns:
            当前阶段标识
        """
        vid = progress.get('videos', {})
        sb = progress.get('storyboards', {})
        clues = progress.get('clues', {})
        chars = progress.get('characters', {})

        if vid.get('completed', 0) == vid.get('total', 0) and vid.get('total', 0) > 0:
            return 'compose'
        elif vid.get('completed', 0) > 0:
            return 'video'
        elif sb.get('completed', 0) > 0:
            return 'storyboard'
        elif clues.get('completed', 0) > 0 or clues.get('total', 0) == 0:
            return 'storyboard'
        elif chars.get('completed', 0) > 0:
            return 'clues'
        return 'characters'

    def enrich_project(self, project_name: str, project: Dict) -> Dict:
        """
        为项目数据注入所有计算字段

        不会修改原始 JSON 文件，仅用于 API 响应。

        Args:
            project_name: 项目名称
            project: 原始项目数据

        Returns:
            注入计算字段后的项目数据
        """
        # 计算整体进度
        progress = self.calculate_project_progress(project_name)
        current_phase = self.calculate_current_phase(progress)

        # 注入 status
        project['status'] = {
            'progress': progress,
            'current_phase': current_phase
        }

        # 为每个 episode 注入计算字段
        for ep in project.get('episodes', []):
            script_file = ep.get('script_file', '')
            if script_file:
                try:
                    script = self.pm.load_script(project_name, script_file)
                    stats = self.calculate_episode_stats(project_name, script)
                    ep['scenes_count'] = stats['scenes_count']
                    ep['status'] = stats['status']
                    ep['duration_seconds'] = stats['duration_seconds']
                except FileNotFoundError:
                    ep['scenes_count'] = 0
                    ep['status'] = 'missing'
                    ep['duration_seconds'] = 0

        return project

    def enrich_script(self, script: Dict) -> Dict:
        """
        为剧本数据注入计算字段

        不会修改原始 JSON 文件，仅用于 API 响应。

        Args:
            script: 原始剧本数据

        Returns:
            注入计算字段后的剧本数据
        """
        content_mode, items = self._select_content_mode_and_items(script)
        default_duration = 4 if content_mode == 'narration' else 8

        total_duration = sum(i.get('duration_seconds', default_duration) for i in items)

        # 注入 metadata 计算字段
        if 'metadata' not in script:
            script['metadata'] = {}

        script['metadata']['total_scenes'] = len(items)
        script['metadata']['estimated_duration_seconds'] = total_duration

        # 聚合 characters_in_episode 和 clues_in_episode（仅用于 API 响应，不存储）
        chars_set = set()
        clues_set = set()

        char_field = 'characters_in_segment' if content_mode == 'narration' else 'characters_in_scene'
        clue_field = 'clues_in_segment' if content_mode == 'narration' else 'clues_in_scene'

        for item in items:
            chars_set.update(item.get(char_field, []))
            clues_set.update(item.get(clue_field, []))

        script['characters_in_episode'] = sorted(chars_set)
        script['clues_in_episode'] = sorted(clues_set)

        return script
