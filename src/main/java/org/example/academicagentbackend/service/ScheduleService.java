package org.example.academicagentbackend.service;  // ← 包名应该是 service.impl

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import org.example.academicagentbackend.mapper.ScheduleItemMapper;  // ← 注意这里
import org.example.academicagentbackend.model.ScheduleItemEntity;
import org.example.academicagentbackend.service.impl.ScheduleServiceImpl;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

@Service
public class ScheduleService
        extends ServiceImpl<ScheduleItemMapper, ScheduleItemEntity>  // ← 这里必须是 ScheduleItemMapper
        implements ScheduleServiceImpl {

    @Override
    public List<ScheduleItemEntity> handleScheduleImage(MultipartFile file) {
        try {
            MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
            body.add("file", new ByteArrayResource(file.getBytes()) {
                @Override
                public String getFilename() {
                    return file.getOriginalFilename();
                }
            });

            // 明确告诉对方这是 multipart 表单上传
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.MULTIPART_FORM_DATA);
            HttpEntity<MultiValueMap<String, Object>> requestEntity = new HttpEntity<>(body, headers);
            RestTemplate restTemplate = new RestTemplate();

            // Python 返回的是课程数组,所以这里接成数组
            ScheduleItemEntity[] parsed = restTemplate.postForObject(
                    "http://127.0.0.1:5000/api/llm/parse_schedule",
                    requestEntity,
                    ScheduleItemEntity[].class
            );

            if (parsed == null || parsed.length == 0) {
                return Collections.emptyList();
            }

            List<ScheduleItemEntity> courses = new ArrayList<>(Arrays.asList(parsed));
            Long currentUserId = 1L;
            for (ScheduleItemEntity course : courses) {
                course.setUserId(currentUserId);   // 每门课都打上用户 id
            }
            this.saveBatch(courses);               // 批量存库

            return courses;

        } catch (IOException e) {
            throw new RuntimeException("处理课程表图片失败", e);
        }
    }
}