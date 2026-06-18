package org.example.academicagentbackend.service;  // ← 包名应该是 service.impl

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import org.example.academicagentbackend.mapper.ScheduleItemMapper;  // ← 注意这里
import org.example.academicagentbackend.model.ScheduleItemEntity;
import org.example.academicagentbackend.service.impl.ScheduleServiceImpl;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpEntity;
import org.springframework.stereotype.Service;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;

@Service
public class ScheduleService
        extends ServiceImpl<ScheduleItemMapper, ScheduleItemEntity>  // ← 这里必须是 ScheduleItemMapper
        implements ScheduleServiceImpl {

    @Override
    public ScheduleItemEntity handleScheduleImage(MultipartFile file) {
        try {
            MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
            body.add("file", new ByteArrayResource(file.getBytes()) {
                @Override
                public String getFilename() {
                    return file.getOriginalFilename();
                }
            });

            HttpEntity<MultiValueMap<String, Object>> requestEntity = new HttpEntity<>(body);
            RestTemplate restTemplate = new RestTemplate();

            ScheduleItemEntity result = restTemplate.postForObject(
                    "http://127.0.0.1:5000/api/llm/parse_schedule",
                    requestEntity,
                    ScheduleItemEntity.class
            );

            if (result != null) {
                Long currentUserId = 1L;
                result.setUserId(currentUserId);
                this.save(result);  // 保存到数据库
            }

            return result;

        } catch (IOException e) {
            throw new RuntimeException("处理课程表图片失败", e);
        }
    }
}