[shaders]
vertex =
    uniform highp mat4 u_modelMatrix;
    uniform highp mat4 u_viewMatrix;
    uniform highp mat4 u_projectionMatrix;

    uniform highp mat4 u_normalMatrix;

    attribute highp vec4 a_vertex;
    attribute highp vec4 a_normal;
    attribute lowp vec4 a_color;

    varying highp vec3 v_vertex;
    varying highp vec3 v_normal;
    varying lowp vec4 v_color;

    void main()
    {
        vec4 world_space_vert = u_modelMatrix * a_vertex;
        gl_Position = u_projectionMatrix * u_viewMatrix * world_space_vert;

        v_vertex = world_space_vert.xyz;
        v_normal = (u_normalMatrix * normalize(a_normal)).xyz;
        v_color = a_color;
    }

fragment =
    uniform mediump vec4 u_ambientColor;
    uniform highp vec3 u_lightPosition;

    uniform mediump float u_opacity;

    varying highp vec3 v_vertex;
    varying highp vec3 v_normal;
    varying lowp vec4 v_color;

    void main()
    {
        mediump vec4 finalColor = vec4(0.0);

        /* Ambient Component */
        finalColor += u_ambientColor;

        highp vec3 normal = normalize(v_normal);
        highp vec3 lightDir = normalize(u_lightPosition - v_vertex);

        /* Diffuse Component — use per-vertex color instead of uniform */
        highp float NdotL = clamp(abs(dot(normal, lightDir)), 0.0, 1.0);
        finalColor += (NdotL * v_color);

        /* Add some of the vertex color as ambient to keep dark areas visible */
        finalColor.rgb += v_color.rgb * 0.3;

        gl_FragColor = finalColor;
        gl_FragColor.a = u_opacity;
    }

vertex41core =
    #version 410
    uniform highp mat4 u_modelMatrix;
    uniform highp mat4 u_viewMatrix;
    uniform highp mat4 u_projectionMatrix;

    uniform highp mat4 u_normalMatrix;

    in highp vec4 a_vertex;
    in highp vec4 a_normal;
    in lowp vec4 a_color;

    out highp vec3 v_vertex;
    out highp vec3 v_normal;
    out lowp vec4 v_color;

    void main()
    {
        vec4 world_space_vert = u_modelMatrix * a_vertex;
        gl_Position = u_projectionMatrix * u_viewMatrix * world_space_vert;

        v_vertex = world_space_vert.xyz;
        v_normal = (u_normalMatrix * normalize(a_normal)).xyz;
        v_color = a_color;
    }

fragment41core =
    #version 410
    uniform mediump vec4 u_ambientColor;
    uniform highp vec3 u_lightPosition;

    uniform mediump float u_opacity;

    in highp vec3 v_vertex;
    in highp vec3 v_normal;
    in lowp vec4 v_color;

    out vec4 frag_color;

    void main()
    {
        mediump vec4 finalColor = vec4(0.0);

        /* Ambient Component */
        finalColor += u_ambientColor;

        highp vec3 normal = normalize(v_normal);
        highp vec3 lightDir = normalize(u_lightPosition - v_vertex);

        /* Diffuse Component — use per-vertex color instead of uniform */
        highp float NdotL = clamp(abs(dot(normal, lightDir)), 0.0, 1.0);
        finalColor += (NdotL * v_color);

        /* Add some of the vertex color as ambient to keep dark areas visible */
        finalColor.rgb += v_color.rgb * 0.3;

        frag_color = finalColor;
        frag_color.a = u_opacity;
    }

[defaults]
u_ambientColor = [0.1, 0.1, 0.1, 1.0]
u_opacity = 0.85

[bindings]
u_modelMatrix = model_matrix
u_viewMatrix = view_matrix
u_projectionMatrix = projection_matrix
u_normalMatrix = normal_matrix
u_lightPosition = light_0_position

[attributes]
a_vertex = vertex
a_normal = normal
a_color = color
