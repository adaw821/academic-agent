package org.example.academicagentbackend.service;

import org.example.academicagentbackend.model.CourseInfoEntity;
import org.example.academicagentbackend.model.ScheduleItemEntity;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpEntity;
import org.springframework.stereotype.Service;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;


@Service
public class ScheduleService {
    public ScheduleItemEntity handleScheduleImage(MultipartFile file){
        try {
            MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();

            body.add("file", new ByteArrayResource(file.getBytes()) {
                @Override
                public String getFilename() {
                    return file.getOriginalFilename(); // 必须重写这个方法，否则 Python 认不出是文件
                }
            });

            // 2. 创建请求实体（不传 headers，让 Spring 自动处理）
            HttpEntity<MultiValueMap<String, Object>> requestEntity = new HttpEntity<>(body);

            // 3. 发送请求
            RestTemplate restTemplate = new RestTemplate();
            CourseInfoEntity result = restTemplate.postForObject(
                    "http://127.0.0.1:5000/api/llm/parse_schedule",
                    requestEntity,
                    CourseInfoEntity.class
            );

            return result;

        } catch (IOException e) {
            // 处理文件读取异常
            throw new RuntimeException("Failed to read uploaded file", e);
        }
    }
}
