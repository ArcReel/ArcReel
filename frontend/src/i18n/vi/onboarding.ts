import type enOnboarding from '@/i18n/en/onboarding';

export default {
  // Các bước hướng dẫn
  'welcome_title': 'Chào mừng đến với [[brand]]',
  'welcome_body': 'Đưa vào một cuốn tiểu thuyết, hệ thống sẽ tách thành phân cảnh, tạo hình ảnh và dựng thành video ngắn. Phần hướng dẫn này chỉ giới thiệu giao diện, không thay đổi bất kỳ dữ liệu nào của bạn.',
  'lobby_create_title': 'Tạo dự án từ đây',
  'lobby_create_body': 'Mỗi dự án đều bắt đầu từ một cuốn tiểu thuyết. Hãy nhập tệp .txt, .docx, .epub hoặc .pdf, [[brand]] sẽ đọc và chia thành từng tập để bạn triển khai.',
  'lobby_demo_title': 'Dự án sẽ trông như thế này',
  'lobby_demo_body': 'Đây là thẻ ví dụ, không phải dự án của bạn. Nhãn hiển thị giai đoạn hiện tại, các số liệu bên dưới theo dõi tiến độ của nhân vật, bối cảnh, đạo cụ và các tập.',
  'lobby_settings_title': 'Cấu hình nhà cung cấp nằm trong Cài đặt',
  'lobby_settings_body': 'Tạo hình ảnh, video và văn bản đều chạy trên nhà cung cấp do bạn chọn. Chấm đỏ trên nút này nghĩa là còn mục bắt buộc chưa được cấu hình — mở Cài đặt sẽ thấy ngay phần còn thiếu.',
  'settings_providers_title': 'Cấu hình một nhà cung cấp',
  'settings_providers_body': 'Cấu hình ít nhất một nhà cung cấp ở đây — nhập API Key và chạy thử kết nối trước khi bắt đầu tạo nội dung.',
  'settings_agent_title': 'Kết nối Agent',
  'settings_agent_body': 'Tạo tổng quan, kịch bản và trả lời trò chuyện đều cần thông tin xác thực Anthropic — hãy thêm vào đây.',
  'finish_title': 'Đến lượt bạn',
  'finish_body': 'Hãy bắt đầu bằng cách nhập một cuốn tiểu thuyết, phần còn lại làm từng bước một. Muốn xem lại, hãy mở Cài đặt → Giới thiệu.',

  // Điều khiển hướng dẫn
  'next': 'Tiếp tục',
  'prev': 'Quay lại',
  'done': 'Hoàn tất',
  'skip': 'Bỏ qua',
  'close': 'Đóng hướng dẫn',
  'progress': 'Bước {{current}} / {{total}}',

  // Thẻ minh hoạ hiển thị trong lúc hướng dẫn
  'demo_section_eyebrow': 'Dự án mẫu',
  'demo_section_note': 'Chỉ hiển thị trong lúc hướng dẫn',
  'demo_project_title': 'Alice ở xứ sở thần tiên',
  'demo_project_style': 'Truyện tranh màu nước',

  // Bàn làm việc minh hoạ chỉ đọc
  'demo_banner_title': 'Dự án minh hoạ · Chỉ đọc',
  'demo_banner_body': 'Đây là dữ liệu mẫu. Chỉnh sửa, tạo, tải lên và xuất đều không khả dụng trong bản minh hoạ.',
  'demo_action_unavailable': 'Không khả dụng trong bản minh hoạ',
  'demo_episode_placeholder': 'Bản minh hoạ chỉ có tập {{episode}}. Tập này chỉ có tiêu đề, chưa có kịch bản và phân cảnh.',

  // Tổng quan dự án minh hoạ
  'demo_overview_synopsis': 'Một buổi chiều nóng nực, Alice đuổi theo con thỏ trắng mặc áo gi-lê, tay giữ đồng hồ quả quýt, rồi rơi xuống một nơi mà kích cỡ, phép tắc và cả logic đều không còn tính. Cô bé lúc lớn lúc nhỏ, lần lượt gặp sâu bướm, Thợ Mũ điên và Nữ hoàng Cơ, cuối cùng làm đổ cả triều đình quân bài trong một phiên xử vô lý.',
  'demo_overview_genre': 'Cổ tích · Phiêu lưu kỳ ảo',
  'demo_overview_theme': 'Sự vô lý của trật tự, và một đứa trẻ không chịu chơi theo luật',
  'demo_overview_world': 'Buổi chiều bên bờ sông thời Victoria mở ra xứ sở thần tiên: hành lang toàn cửa khoá dưới hang thỏ, khu vườn mắc kẹt ở giờ trà chiều, triều đình quân bài trị vì bằng croquet và những án chặt đầu. Chất liệu truyện tranh màu nước, thấy rõ vân giấy, viền loang màu.',

  // Tiêu đề tập minh hoạ
  'demo_episode_1_title': 'Xuống hang thỏ',
  'demo_episode_2_title': 'Hồ nước mắt',
  'demo_episode_3_title': 'Lời khuyên của sâu bướm',
  'demo_episode_4_title': 'Nhà bếp của Nữ công tước',
  'demo_episode_5_title': 'Tiệc trà điên',
  'demo_episode_6_title': 'Sân croquet của Nữ hoàng',
  'demo_episode_7_title': 'Chuyện của Rùa Giả',
  'demo_episode_8_title': 'Ai đã lấy bánh nhân?',

  // Nhân vật minh hoạ
  'demo_character_1_name': 'Alice',
  'demo_character_1_desc': 'Cô bé người Anh bảy tuổi, váy xanh yếm trắng, tóc vàng nhạt cài băng đen. Tò mò nhiều hơn thận trọng: gặp chuyện vô lý thì tranh luận trước, không xong thì tự đi tiếp.',
  'demo_character_1_voice': 'Giọng trẻ trong sáng, nhịp hơi nhanh, tò mò hơn là sợ',
  'demo_character_2_name': 'Thỏ Trắng',
  'demo_character_2_desc': 'Con thỏ trắng mặc áo gi-lê kẻ ô, một chân giữ đồng hồ quả quýt, chân kia cầm đôi găng, lúc nào cũng muộn. Sự sốt sắng hiện cả lên đôi tai; nói như đang chạy.',
  'demo_character_2_voice': 'Giọng cao và gấp, hụt hơi, lẩm bẩm một mình',
  'demo_character_3_name': 'Mèo Cheshire',
  'demo_character_3_desc': 'Con mèo lớn vằn xám tím, nụ cười kéo từ tai này sang tai kia. Biến mất từng phần, chỉ để nụ cười lơ lửng giữa không khí.',
  'demo_character_3_voice': 'Giọng trầm kéo dài, cuối câu vút lên, luôn như đang trêu',
  'demo_character_4_name': 'Nữ hoàng Cơ',
  'demo_character_4_desc': 'Người trị vì triều đình quân bài, váy dài hoa văn hình trái tim đỏ đen, tay nắm trượng. Phán quyết chỉ một câu: chặt đầu nó đi.',
  'demo_character_4_voice': 'Giọng nữ vang và nóng nảy; mở miệng là ra lệnh',

  // Bối cảnh minh hoạ
  'demo_scene_1_name': 'Bờ sông dưới rặng liễu',
  'demo_scene_1_desc': 'Bờ cỏ một buổi chiều nóng nực, cành liễu rủ xuống mặt nước, dòng sông chậm và sáng, một quyển sách mở ra không có tranh cũng không có đối thoại.',
  'demo_scene_2_name': 'Hành lang dưới hang thỏ',
  'demo_scene_2_desc': 'Hành lang thấp dưới đáy hang, hai bên toàn cửa khoá, cuối hành lang là chiếc bàn kính ba chân với một chiếc chìa khoá vàng tí xíu. Chỉ một cánh cửa cao mười lăm inch cho ánh sáng lọt qua.',
  'demo_scene_3_name': 'Khu vườn tiệc trà điên',
  'demo_scene_3_desc': 'Chiếc bàn dài dưới tán cây lớn, tách và đĩa dồn hết về cuối bàn, ghế nhiều hơn số người ngồi. Mọi đồng hồ dừng ở sáu giờ; trà thì lúc nào cũng vừa rót.',

  // Đạo cụ minh hoạ
  'demo_prop_1_name': 'Đồng hồ quả quýt của Thỏ Trắng',
  'demo_prop_1_desc': 'Đồng hồ quả quýt bằng đồng, dây xích mòn bóng, mặt trong nắp có khắc chữ. Kim chạy nhanh hơn mọi loại đồng hồ, và Thỏ vừa nhìn vừa hét là mình muộn.',
  'demo_prop_2_name': 'Lọ "UỐNG TÔI"',
  'demo_prop_2_desc': 'Lọ thuỷ tinh nhỏ, cổ lọ buộc nhãn giấy ghi "UỐNG TÔI". Vị như bánh anh đào lẫn dứa, uống xong người chỉ còn cao mười inch.',
  'demo_prop_3_name': 'Chim hồng hạc làm vồ',
  'demo_prop_3_desc': 'Con hồng hạc kẹp dưới cánh tay để làm vồ croquet. Vừa vuốt thẳng cổ nó lại tự xoắn về; mỗi cú đánh đều phải thương lượng trước.',

  // Phân cảnh minh hoạ — tập 1
  'demo_shot_1_text': 'Buổi chiều ấy rất nóng. Alice ngồi bên bờ sông cạnh chị gái, quyển sách chị đọc không có tranh cũng không có đối thoại, và cô bé bắt đầu buồn ngủ.',
  'demo_shot_1_image': 'Bờ sông ngày hè; Alice váy xanh yếm trắng ngủ gà trên sườn cỏ, chị gái đọc sách bên cạnh, cành liễu rủ xuống mặt nước. Chất liệu truyện tranh màu nước, viền loang mềm, thấy rõ vân giấy.',
  'demo_shot_1_lighting': 'Ngược sáng chiều muộn, bóng lá lốm đốm trên cỏ',
  'demo_shot_1_ambiance': 'Uể oải, ấm, lơ mơ ngủ',
  'demo_shot_1_video': 'Máy lia chậm sang phải dọc bờ sông; gió lay cỏ và vạt váy, mắt cô bé từ từ nhắm lại.',
  'demo_shot_1_audio': 'Tiếng nước chảy, ve kêu xa, tiếng lật trang sách',
  'demo_shot_2_text': 'Một con thỏ trắng mặc áo gi-lê chạy ngang qua, rút đồng hồ ra xem rồi lẩm bẩm: chết rồi, chết rồi, mình sẽ muộn mất.',
  'demo_shot_2_image': 'Thỏ Trắng đứng thẳng trong áo gi-lê kẻ ô, một chân giơ cao chiếc đồng hồ đồng, rảo bước qua bụi cỏ; Alice ngồi dậy ở hậu cảnh. Chất liệu truyện tranh màu nước.',
  'demo_shot_2_lighting': 'Nắng sáng, viền lông thỏ hắt sáng',
  'demo_shot_2_ambiance': 'Bất ngờ, hài, gấp gáp',
  'demo_shot_2_video': 'Máy chạy theo Thỏ Trắng, nó cúi xem đồng hồ rồi ngẩng lên chạy tiếp, Alice quay đầu dõi theo.',
  'demo_shot_2_audio': 'Bước chân lích rích, đồng hồ tích tắc, tiếng thỏ thở dốc',
  'demo_shot_3_text': 'Alice bật dậy đuổi theo, vừa kịp thấy Thỏ Trắng lẻn vào một hang thỏ lớn dưới bờ rào. Cô bé chui theo mà không hề nghĩ làm sao ra được.',
  'demo_shot_3_image': 'Hang thỏ tối om dưới bờ rào; Alice chống tay nhoài người vào, vạt váy bốc lên, cái đuôi Thỏ Trắng vừa biến mất bên trong. Chất liệu truyện tranh màu nước.',
  'demo_shot_3_lighting': 'Tương phản mạnh giữa cỏ sáng và miệng hang tối đen',
  'demo_shot_3_ambiance': 'Khoảnh khắc tò mò thắng nỗi sợ',
  'demo_shot_3_video': 'Máy đẩy dần về miệng hang khi cô bé chống một tay xuống đất và nhoài vào; khuôn hình bị bóng tối nuốt mất.',
  'demo_shot_3_audio': 'Lá xào xạc, đất rơi lả tả, tiếng vọng khép lại',
  'demo_shot_4_text': 'Hang đi thẳng như đường hầm rồi bất chợt dốc xuống. Cô bé rơi rất chậm, kịp nhìn rõ những tủ, giá sách và tấm bản đồ treo trên vách.',
  'demo_shot_4_image': 'Mặt cắt giếng sâu; Alice rơi chậm với vạt váy xoè ra, vách giếng xếp đầy tủ, giá sách, bản đồ và một chiếc đèn nhỏ. Chất liệu truyện tranh màu nước, càng xuống càng tối.',
  'demo_shot_4_lighting': 'Một vòng sáng trời phía trên, tối dần xuống dưới, đèn hắt những vệt ấm',
  'demo_shot_4_ambiance': 'Lơ lửng, mất trọng lượng, thời gian bị kéo dài',
  'demo_shot_4_video': 'Máy hạ theo cô bé; tủ và giá sách lần lượt trôi ngược lên khỏi khuôn hình.',
  'demo_shot_4_audio': 'Gió trong lòng giếng, vải váy phất, tiếng nước nhỏ giọt xa',
  'demo_shot_5_text': 'Dưới đáy là một hành lang dài toàn cửa, và cửa nào cũng khoá. Trên bàn kính cô bé tìm thấy chiếc chìa khoá vàng tí xíu — nó chỉ vừa một cánh cửa cao mười lăm inch.',
  'demo_shot_5_image': 'Cận chiếc bàn kính ba chân với chìa khoá vàng tí xíu và một lọ nhỏ buộc nhãn giấy; phía sau, cánh cửa thấp để ánh sáng lọt ra. Chất liệu truyện tranh màu nước.',
  'demo_shot_5_lighting': 'Một vệt sáng ấm qua khe cửa, xung quanh chìm vào tối',
  'demo_shot_5_ambiance': 'Yên ắng, chật chội, một tia hy vọng',
  'demo_shot_5_video': 'Máy đứng yên; vệt sáng bò chậm qua chiếc chìa khoá, bụi trôi trong luồng sáng.',
  'demo_shot_5_audio': 'Tiếng vọng hành lang trống, tiếng chìa khoá va vào kính',
  'demo_shot_6_text': 'Nhãn giấy trên cổ lọ ghi "UỐNG TÔI". Cô bé uống một hớp, cảm thấy mình xếp lại như chiếc kính viễn vọng, co tới chỉ còn cao mười inch.',
  'demo_shot_6_image': 'Alice giơ lọ "UỐNG TÔI" lên uống; trong cùng khuôn hình, người cô bé co lại, chiếc váy đổ xuống quanh chân, cánh cửa thấp vừa khít với cô. Chất liệu truyện tranh màu nước.',
  'demo_shot_6_lighting': 'Ánh ấm tràn ra từ cánh cửa thấp, viền người hắt sáng',
  'demo_shot_6_ambiance': 'Một cú ngoặt gây ngạc nhiên hơn là sợ',
  'demo_shot_6_video': 'Máy lùi ra khi cô bé co lại; váy đổ xuống, góc nhìn chuyển từ ngang mắt sang ngước lên cánh cửa thấp.',
  'demo_shot_6_audio': 'Một tiếng nuốt, vải trượt, chiếc lọ rỗng đặt xuống đất',

  // Mục trong Cài đặt → Giới thiệu
  'replay_title': 'Hướng dẫn sử dụng',
  'replay_desc': 'Xem lại phần hướng dẫn lần đầu. Chỉ giới thiệu giao diện, không thay đổi dữ liệu.',
  'replay_action': 'Xem lại hướng dẫn',
} satisfies Record<keyof typeof enOnboarding, string>;
